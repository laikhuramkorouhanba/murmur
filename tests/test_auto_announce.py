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

    def sound():
        heard["sounds"] += 1
        return "sounddevice"

    monkeypatch.setattr(app.voice, "play_soft_sound", sound)
    monkeypatch.setattr(app, "summarise", lambda text: "The test failed.")
    yield heard
    personality.set_mode(personality.Mode.SMOOTH)


def run(text: str, seconds: float) -> None:
    app.announce_finished_command(FinishedCommand("/dev/ttys001", seconds), read_text=lambda: text)


def test_quick_success_is_silent(quiet_audio) -> None:
    run(PASSING, 1.0)

    assert quiet_audio == {"spoken": [], "sounds": 0}


def test_medium_success_plays_only_the_soft_sound(quiet_audio) -> None:
    run(PASSING, 5.0)

    assert quiet_audio == {"spoken": [], "sounds": 1}


def test_long_success_says_one_short_done_line(quiet_audio) -> None:
    personality.set_mode(personality.Mode.FOCUSED)

    run(PASSING, 40.0)

    assert quiet_audio == {"spoken": ["Done, no errors."], "sounds": 0}


def test_long_success_in_smooth_mode_uses_a_smooth_line(quiet_audio) -> None:
    run(PASSING, 40.0)

    assert quiet_audio["spoken"][0] in personality._SMOOTH_DONE_LINES


@pytest.mark.parametrize("seconds", [1.0, 30.0])
def test_a_failure_gets_the_full_announcement(quiet_audio, capsys, seconds) -> None:
    run(FAILING, seconds)

    assert len(quiet_audio["spoken"]) == 1
    assert "line 6" in quiet_audio["spoken"][0]
    assert "demo/failing_check.py:6: AssertionError" in capsys.readouterr().out


def test_closed_tab_is_reported_without_speaking(quiet_audio, capsys) -> None:
    def closed():
        raise RuntimeError("That Terminal tab was closed before Murmur could read it.")

    app.announce_finished_command(FinishedCommand("/dev/ttys001", 9.0), read_text=closed)

    assert quiet_audio == {"spoken": [], "sounds": 0}
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


def test_menu_toggle_saves_the_choice_and_says_it_in_words(capsys) -> None:
    menu_app = app.MurmurApp()
    assert menu_app.auto_item.state == 1
    assert str(menu_app.auto_item._menuitem.accessibilityLabel()) == "Auto-announce finished commands, on"

    menu_app.toggle_auto(None)

    assert settings.get("auto_announce") is False
    assert menu_app.auto_item.state == 0
    assert str(menu_app.auto_item._menuitem.accessibilityLabel()) == "Auto-announce finished commands, off"
    assert "Murmur auto-announce: off. Turn it on in the Murmur menu." in capsys.readouterr().out
