import pytest

from murmur import app, personality, settings, voice
from murmur.monitor import FinishedCommand

PASSING = "me@mac proj % pytest -q\n3 passed in 0.10s\nme@mac proj % "
FAILING = (
    "me@mac proj % pytest -q\n"
    "FAILED demo/failing_check.py::test_total_includes_the_tip\n"
    "E       assert 18.0 == 19.8\n"
    "demo/failing_check.py:6: AssertionError\n"
    "me@mac proj % "
)


@pytest.fixture(autouse=True)
def quiet_audio(monkeypatch):
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_CANCELLED.clear()
    voice.reset_stop()
    heard = {"spoken": [], "sounds": 0}
    monkeypatch.setattr(app.voice, "speak", lambda text, **_k: (heard["spoken"].append(text) or "OpenAI"))

    def sound(kind):
        def play():
            heard["sounds"] += 1
            heard.setdefault("order", []).append(kind)
            return "sounddevice"

        return play

    monkeypatch.setattr(app.voice, "play_pass_sound", sound("pass"))
    monkeypatch.setattr(app.voice, "play_fail_sound", sound("fail"))
    monkeypatch.setattr(app, "summarise", lambda text: "The test failed.")
    yield heard
    personality.set_mode(personality.Mode.BRIEF)


def run(text: str, seconds: float) -> None:
    app.announce_finished_command(FinishedCommand("/dev/ttys001", seconds), read_text=lambda: text)


LS = "me@mac proj % ls\nREADME.md\tshop.py\tshop_demo_check.py\nme@mac proj % "
LONG = "me@mac proj % make\n" + "\n".join(f"compiled part {n}" for n in range(30)) + "\nme@mac proj % "
NOTHING = "me@mac proj % cd src\nme@mac proj % "


def test_short_success_is_read_word_for_word_after_the_pass_sound(quiet_audio) -> None:
    run(LS, 0.3)

    assert quiet_audio["spoken"] == ["README.md\tshop.py\tshop_demo_check.py"]
    assert quiet_audio["order"] == ["pass"]


def test_a_passing_test_run_is_read_too(quiet_audio) -> None:
    run(PASSING, 1.0)

    assert quiet_audio["spoken"] == ["3 passed in 0.10s"]


def test_long_success_says_how_many_lines(quiet_audio) -> None:
    run(LONG, 3.0)

    assert quiet_audio["spoken"] == ["Done, 30 lines."]
    assert quiet_audio["sounds"] == 1


def test_no_output_stays_silent(quiet_audio) -> None:
    run(NOTHING, 0.2)

    assert quiet_audio == {"spoken": [], "sounds": 0}


def test_a_long_command_with_no_output_says_done(quiet_audio) -> None:
    run(NOTHING, 40.0)

    assert quiet_audio["spoken"] == ["Done."]


def test_friendly_long_success_gets_an_opener(quiet_audio) -> None:
    personality.set_mode(personality.Mode.FRIENDLY)

    run(LONG, 3.0)

    assert "Done, 30 lines." in quiet_audio["spoken"][0]
    assert quiet_audio["spoken"][0] != "Done, 30 lines."


@pytest.mark.parametrize("seconds", [1.0, 30.0])
def test_a_failure_gets_the_full_announcement(quiet_audio, capsys, seconds) -> None:
    run(FAILING, seconds)

    assert quiet_audio["spoken"] == ["Failed. failing check dot py, line 6. Expected 19.8, got 18."]
    assert quiet_audio["order"] == ["fail"]
    assert "demo/failing_check.py:6: AssertionError" in capsys.readouterr().out


def test_closed_tab_is_reported_without_speaking(quiet_audio, capsys) -> None:
    def closed():
        raise RuntimeError("That Terminal tab was closed before Murmur could read it.")

    app.announce_finished_command(FinishedCommand("/dev/ttys001", 9.0), read_text=closed)

    assert quiet_audio["spoken"] == [] and quiet_audio["sounds"] == 0
    assert "closed before Murmur could read it" in capsys.readouterr().out


def test_no_auto_announcement_while_murmur_is_already_speaking(monkeypatch, capsys) -> None:
    started = []
    monkeypatch.setattr(app.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(1)})())
    app._JOB_ACTIVE = True  # pretend the welcome or an announcement is playing

    assert not app.start_auto_announcement(FinishedCommand("/dev/ttys001", 30.0))

    assert started == []
    assert not app._JOB_CANCELLED.is_set()  # it must not stop the current speech either
    assert "Auto-announce skipped: Murmur is already speaking." in capsys.readouterr().out


def test_hotkey_still_stops_speech_during_an_auto_announcement(monkeypatch) -> None:
    stops = []
    monkeypatch.setattr(app.voice, "stop_speech", lambda: stops.append(1))
    app._JOB_ACTIVE = True  # an auto-announcement is talking

    app.start_announcement(stop_if_running=True)

    assert stops == [1]
    assert app._JOB_CANCELLED.is_set()
