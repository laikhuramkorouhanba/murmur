import pytest

from murmur import app, personality, settings, voice, welcome


@pytest.fixture(autouse=True)
def reset_job_state():
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_CANCELLED.clear()
    voice.reset_stop()
    yield
    personality.set_mode(personality.Mode.SMOOTH)


_STARTED = []


class RecordingThread:
    """Remembers the job instead of running it, so the test can run it afterwards."""

    def __init__(self, *, target, name, daemon):
        self.target = target

    def start(self):
        _STARTED.append(self.target)


def run_started_jobs():
    while _STARTED:
        _STARTED.pop(0)()


def test_welcome_names_both_shortcuts_and_every_permission() -> None:
    text = welcome.welcome_text(personality.Mode.FOCUSED, app_name="Visual Studio Code", missing=[])

    assert "Command Option Shift M" in text
    assert "tap Right Command twice" in text
    assert "Automation. Allow Visual Studio Code to control Terminal" in text
    assert "Accessibility, and Input Monitoring. Turn on Visual Studio Code in both" in text
    assert "System Settings, then Privacy and Security" in text
    assert "quit and reopen Visual Studio Code" in text
    assert "Replay welcome" in text
    assert "cool cat" not in text


def test_welcome_follows_the_double_tap_choice_and_personality() -> None:
    settings.set("double_tap_key", "right_shift")

    text = welcome.welcome_text(personality.Mode.SMOOTH, app_name="Terminal", missing=[])

    assert "tap Right Shift twice" in text
    assert text.startswith("Hey there, I'm Murmur.")


def test_welcome_says_which_permissions_are_still_missing() -> None:
    text = welcome.welcome_text(app_name="Terminal", missing=["Input Monitoring"])

    assert "Right now, Input Monitoring still needs turning on." in text


def test_welcome_plays_on_first_run_only(monkeypatch, capsys) -> None:
    spoken = []
    monkeypatch.setattr(app.threading, "Thread", RecordingThread)
    monkeypatch.setattr(app.voice, "speak", lambda text, **_kwargs: (spoken.append(text) or "OpenAI"))
    monkeypatch.setattr(app.welcome, "welcome_text", lambda mode: f"Welcome in {mode.value}.")

    assert app.welcome_if_first_run()
    run_started_jobs()
    assert not app.welcome_if_first_run()

    assert spoken == ["Welcome in Smooth."]
    assert settings.get("welcome_done") is True
    assert "Welcome in Smooth." in capsys.readouterr().out


def test_replay_welcome_menu_item_plays_it_again(monkeypatch) -> None:
    spoken = []
    settings.set("welcome_done", True)
    monkeypatch.setattr(app.threading, "Thread", RecordingThread)
    monkeypatch.setattr(app.voice, "speak", lambda text, **_kwargs: (spoken.append(text) or "OpenAI"))
    monkeypatch.setattr(app.welcome, "welcome_text", lambda mode: "Hello again.")

    app.MurmurApp().replay_welcome(None)
    run_started_jobs()

    assert spoken == ["Hello again."]
