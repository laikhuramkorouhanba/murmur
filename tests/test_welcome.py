import pytest

from murmur import app, personality, settings, voice, welcome


@pytest.fixture(autouse=True)
def reset_job_state():
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_CANCELLED.clear()
    voice.reset_stop()
    yield
    personality.set_mode(personality.Mode.BRIEF)


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


def test_welcome_is_short_and_explains_the_new_defaults() -> None:
    text = welcome.welcome_text(personality.Mode.BRIEF, app_name="Visual Studio Code", missing=[])

    assert text == (
        "Murmur ready. Double tap Right Command, then Space repeats, R reads all, F fixes, U undoes. "
        "Control stops speech. Backups: Command Option Shift M, F and R."
    )
    assert "twice twice" not in text
    assert len(text.split()) <= 30


def test_welcome_follows_the_double_tap_choice_and_style() -> None:
    settings.set("double_tap_key", "right_shift")

    text = welcome.welcome_text(personality.Mode.FRIENDLY, app_name="Terminal", missing=[])

    assert "Double tap Right Shift, then" in text
    assert text.startswith("Hey, Murmur here.")


def test_welcome_says_which_permissions_are_still_missing() -> None:
    text = welcome.welcome_text(app_name="Terminal", missing=["Input Monitoring"])

    assert "Missing Input Monitoring" in text
    assert "Accessibility" not in text
    assert "Automation" not in text
    assert "System Settings → Privacy & Security" in text


def test_welcome_gives_separate_advice_for_missing_automation_and_keyboard_access() -> None:
    text = welcome.welcome_text(
        app_name="Terminal", missing=["Automation", "Accessibility", "Input Monitoring"]
    )

    assert "Missing Automation: allow Terminal to control Terminal" in text
    assert "Missing Accessibility and Input Monitoring" in text
    assert len(text.split()) <= 70


def test_welcome_checks_only_permissions_macos_reports_as_missing(monkeypatch) -> None:
    monkeypatch.setattr(welcome.hotkey, "missing_permissions", lambda: ["Accessibility"])
    monkeypatch.setattr(welcome.watcher, "automation_permission_missing", lambda: False)

    text = welcome.welcome_text(personality.Mode.BRIEF, app_name="Terminal")

    assert "Missing Accessibility" in text
    assert "Automation" not in text
    assert "Input Monitoring" not in text


def test_welcome_mentions_automation_only_when_apple_events_are_blocked(monkeypatch) -> None:
    monkeypatch.setattr(welcome.hotkey, "missing_permissions", lambda: [])
    monkeypatch.setattr(welcome.watcher, "automation_permission_missing", lambda: True)

    text = welcome.welcome_text(personality.Mode.BRIEF, app_name="Terminal")

    assert "Missing Automation" in text
    assert "Accessibility" not in text
    assert "Input Monitoring" not in text


def test_welcome_plays_on_first_run_only(monkeypatch, capsys) -> None:
    spoken = []
    monkeypatch.setattr(app.threading, "Thread", RecordingThread)
    monkeypatch.setattr(app.voice, "speak", lambda text, **_kwargs: (spoken.append(text) or "OpenAI"))
    monkeypatch.setattr(app.welcome, "welcome_text", lambda mode: f"Welcome in {mode.value}.")

    assert app.welcome_if_first_run()
    run_started_jobs()
    assert not app.welcome_if_first_run()

    assert spoken == ["Welcome in Brief."]
    assert settings.get("welcome_done") is True
    assert "Welcome in Brief." in capsys.readouterr().out


def test_replay_welcome_menu_item_plays_it_again(monkeypatch) -> None:
    spoken = []
    settings.set("welcome_done", True)
    monkeypatch.setattr(app.threading, "Thread", RecordingThread)
    monkeypatch.setattr(app.voice, "speak", lambda text, **_kwargs: (spoken.append(text) or "OpenAI"))
    monkeypatch.setattr(app.welcome, "welcome_text", lambda mode: "Hello again.")

    app.MurmurApp().replay_welcome(None)
    run_started_jobs()

    assert spoken == ["Hello again."]
