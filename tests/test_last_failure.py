from murmur import app, fixer
from murmur.monitor import FinishedCommand

FAILING = (
    "me@mac shop % pytest -q\n"
    "E       assert 18.0 == 19.8\n"
    "demo/failing_check.py:6: AssertionError\n"
    "1 failed in 0.05s\n"
    "me@mac shop % "
)
PASSING = "me@mac shop % pytest -q\n3 passed in 0.10s\nme@mac shop % "


def quiet(monkeypatch):
    monkeypatch.setattr(app.voice, "speak", lambda text, **_k: "OpenAI")
    monkeypatch.setattr(app.voice, "play_soft_sound", lambda: "sounddevice")
    monkeypatch.setattr(app, "summarise", lambda text: "The test failed.")


def test_a_failure_is_remembered_with_its_tab_folder_and_command(monkeypatch) -> None:
    quiet(monkeypatch)
    monkeypatch.setattr(app, "_lookup_front_tty", lambda: "/dev/ttys004")
    monkeypatch.setattr(app, "_lookup_folder", lambda tty: "/Users/ada/shop")

    app.announce_last_output(read_text=lambda: FAILING)

    failure = fixer.last_failure()
    assert failure.tty == "/dev/ttys004"
    assert failure.folder == "/Users/ada/shop"
    assert failure.command == "pytest -q"
    assert "demo/failing_check.py:6: AssertionError" in failure.output


def test_auto_announce_uses_the_tab_that_finished(monkeypatch) -> None:
    quiet(monkeypatch)
    folders = []
    monkeypatch.setattr(app, "_lookup_folder", lambda tty: (folders.append(tty) or "/srv/app"))

    app.announce_finished_command(FinishedCommand("/dev/ttys009", 5.0), read_text=lambda: FAILING)

    assert folders == ["/dev/ttys009"]
    assert fixer.last_failure().folder == "/srv/app"


def test_an_announced_success_forgets_the_old_failure(monkeypatch) -> None:
    quiet(monkeypatch)
    app.announce_last_output(read_text=lambda: FAILING)
    assert fixer.last_failure() is not None

    app.announce_last_output(read_text=lambda: PASSING)

    assert fixer.last_failure() is None


def test_a_silent_quick_success_keeps_the_failure(monkeypatch) -> None:
    quiet(monkeypatch)
    app.announce_last_output(read_text=lambda: FAILING)

    app.announce_finished_command(FinishedCommand("/dev/ttys001", 0.5), read_text=lambda: PASSING)

    assert fixer.last_failure() is not None


def test_hotkey_announcement_remembers_a_failure_behind_a_virtual_env_prompt(monkeypatch) -> None:
    """Regression from the live test: ⌘⌥⇧M, then ⌘⌥⇧F said "Nothing to fix"."""
    from pathlib import Path

    from murmur import app, fixer

    history = (Path(__file__).parent / "samples" / "zsh_venv_prompt.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(app, "_lookup_front_tty", lambda: "/dev/ttys005")
    monkeypatch.setattr(app, "_lookup_folder", lambda tty: "/Users/ada/murmur-fixme-demo")
    monkeypatch.setattr(app, "summarise", lambda text: "One test failed in shop_demo_check.py.")
    monkeypatch.setattr(app.voice, "speak", lambda *a, **k: "OpenAI")

    app.announce_last_output(read_text=lambda: history)  # the hotkey path: no tty given

    failure = fixer.last_failure()
    assert failure is not None
    assert failure.tty == "/dev/ttys005"
    assert failure.folder == "/Users/ada/murmur-fixme-demo"
    assert failure.command == "cd ~/murmur-fixme-demo && uv run --project ~/murmur pytest -q shop_demo_check.py"
    assert "AssertionError" in failure.output
