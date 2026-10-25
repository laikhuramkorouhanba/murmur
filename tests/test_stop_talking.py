"""The Control key: stop speech now, but never throw away a running fix."""

import threading
import time

import pytest

from murmur import app, voice


@pytest.fixture(autouse=True)
def job_state():
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_NAME = None
        app._JOB_CANCELLED.clear()
    voice.reset_stop()
    voice._HUSH_EVENT.clear()
    yield
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_NAME = None
        app._JOB_CANCELLED.clear()
    voice.reset_stop()
    voice._HUSH_EVENT.clear()


def test_control_cancels_an_announcement(monkeypatch) -> None:
    stops = []
    monkeypatch.setattr(app.voice, "stop_speech", lambda: stops.append(1))
    app._JOB_ACTIVE, app._JOB_NAME = True, "murmur-announcement"

    app.stop_talking()

    assert stops == [1]
    assert app._JOB_CANCELLED.is_set()


def test_control_during_a_fix_only_quiets_the_current_line(monkeypatch) -> None:
    hushes = []
    monkeypatch.setattr(app.voice, "hush", lambda: hushes.append(1))
    monkeypatch.setattr(app.voice, "stop_speech", lambda: pytest.fail("the fix must keep going"))
    app._JOB_ACTIVE, app._JOB_NAME = True, "murmur-fix"

    app.stop_talking()

    assert hushes == [1]
    assert not app._JOB_CANCELLED.is_set()


def test_a_hushed_line_stops_but_the_next_line_still_speaks(monkeypatch) -> None:
    from murmur import settings

    settings.set("engine", "mac")
    started = threading.Event()
    finished = []

    class SlowSay:
        def __init__(self, command, **_kwargs):
            self.command = command
            self.done = threading.Event()
            started.set()

        def wait(self):
            self.done.wait(5)
            return 0 if not hasattr(self, "killed") else -15

        def poll(self):
            return None

        def terminate(self):
            self.killed = True
            self.done.set()

    monkeypatch.setattr(voice.subprocess, "Popen", SlowSay)
    worker = threading.Thread(target=lambda: finished.append(voice.speak("Fixing.")))
    worker.start()
    started.wait(2)
    time.sleep(0.05)

    voice.hush()
    worker.join(2)

    assert finished == ["none (stopped)"]
    assert not voice._STOP_EVENT.is_set()  # the next line is allowed to speak
    started.clear()

    class QuickSay(SlowSay):
        def wait(self):
            return 0

    monkeypatch.setattr(voice.subprocess, "Popen", QuickSay)
    assert voice.speak("Fixed.") == "macOS say"
