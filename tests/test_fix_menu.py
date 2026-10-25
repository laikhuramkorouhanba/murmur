"""Fix it's hotkey, menu items, Undo, and how it shares the job lock."""

from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

from murmur import app, config, fixer, hotkey, settings, voice
from test_hotkey import FAKE_KEYBOARD, FakeKey


letter = namedtuple("KeyCode", "char vk")


@pytest.fixture(autouse=True)
def reset_job_state():
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_NAME = None
        app._JOB_CANCELLED.clear()
    voice.reset_stop()
    yield
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_NAME = None


# --- the ⌘⌥⇧F hotkey -------------------------------------------------------


def press_with_all_three(watcher, key) -> None:
    for modifier in (FakeKey.cmd, FakeKey.alt, FakeKey.shift):
        watcher.press(modifier)
    watcher.press(key)
    watcher.release(key)


def test_cmd_option_shift_f_asks_for_a_fix_not_an_announcement() -> None:
    announced, fixes = [], []
    watcher = hotkey.ShortcutWatcher(FAKE_KEYBOARD, lambda: announced.append(1), on_fix=lambda: fixes.append(1))

    press_with_all_three(watcher, letter("Ï", config.FIX_KEY_CODE))  # what macOS types for ⌥⇧F

    assert fixes == [1] and announced == []


def test_f_without_all_three_modifiers_does_nothing() -> None:
    fixes = []
    watcher = hotkey.ShortcutWatcher(FAKE_KEYBOARD, lambda: None, on_fix=lambda: fixes.append(1))

    watcher.press(FakeKey.cmd)
    watcher.press(letter("f", config.FIX_KEY_CODE))

    assert fixes == []


# --- start_fix and the job lock -----------------------------------------------


class RecordingThread:
    """Remembers each job instead of running it."""

    started: list[str] = []

    def __init__(self, *, target, name, daemon):
        self.name = name

    def start(self):
        RecordingThread.started.append(self.name)


@pytest.fixture
def threads(monkeypatch):
    RecordingThread.started = []
    monkeypatch.setattr(app.threading, "Thread", RecordingThread)
    return RecordingThread.started


def busy_with(name: str) -> None:
    with app._JOB_LOCK:
        app._JOB_ACTIVE = True
        app._JOB_NAME = name


def test_fix_starts_straight_away_when_murmur_is_quiet(threads) -> None:
    assert app.start_fix()
    assert threads == ["murmur-fix"]


def test_fix_during_a_fix_cancels_it(threads) -> None:
    busy_with("murmur-fix")

    assert not app.start_fix()
    assert app._JOB_CANCELLED.is_set()
    assert threads == []


def test_fix_while_reading_the_error_stops_talking_then_fixes(threads) -> None:
    busy_with("murmur-announcement")

    assert app.start_fix()
    assert app._JOB_CANCELLED.is_set()  # the reading stops
    assert threads == ["murmur-fix-waiter"]  # and the fix starts as soon as it has


def test_any_other_shortcut_during_a_fix_cancels_it(threads) -> None:
    busy_with("murmur-fix")

    app.start_announcement(stop_if_running=True)

    assert app._JOB_CANCELLED.is_set()


# --- the menu items -----------------------------------------------------------


def spoken(item) -> str:
    return str(item._menuitem.accessibilityLabel())


def dimmed(item) -> bool:
    return item._menuitem.action() is None


def test_fix_items_are_dimmed_and_say_why_when_there_is_nothing_to_do() -> None:
    menu_app = app.MurmurApp()

    assert menu_app.fix_item.title == "Fix last error (⌘⌥⇧F)"
    for item in (menu_app.fix_item, menu_app.undo_item, menu_app.diff_item):
        assert dimmed(item)
    assert spoken(menu_app.fix_item).endswith("Unavailable: no failed command to fix")
    assert spoken(menu_app.undo_item) == "Undo last fix. Unavailable: no fix to undo"


def test_fix_items_light_up_when_they_can_be_used(tmp_path: Path) -> None:
    menu_app = app.MurmurApp()
    fixer.remember_failure(fixer.LastFailure(None, str(tmp_path), "pytest", "boom"))
    fixer.remember_fix(fixer.LastFix(tmp_path, "a", "b", ["shop.py"]))
    fixer.DIFF_PATH.write_text("diff\n")

    menu_app.show_fix_choices()

    for item in (menu_app.fix_item, menu_app.undo_item, menu_app.diff_item):
        assert not dimmed(item)
    assert spoken(menu_app.fix_item) == "Fix last error with Codex. Shortcut: Command Option Shift F"
    assert spoken(menu_app.diff_item) == "Open last fix diff in TextEdit"


def test_fix_items_sit_together_in_the_menu() -> None:
    titles = list(app.MurmurApp().menu.keys())

    start = titles.index("Fix last error (⌘⌥⇧F)")
    assert titles[start : start + 3] == ["Fix last error (⌘⌥⇧F)", "Undo last fix", "Open last fix diff"]


def test_open_diff_uses_textedit(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(app.subprocess, "run", lambda command, check: calls.append(command))
    fixer.DIFF_PATH.write_text("diff\n")

    assert app.open_last_fix_diff()
    assert calls == [["open", "-e", str(fixer.DIFF_PATH)]]


def test_open_diff_does_nothing_without_a_diff(monkeypatch) -> None:
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: pytest.fail("should not open"))

    assert not app.open_last_fix_diff()


def test_startup_warns_when_codex_is_not_ready(monkeypatch, capsys) -> None:
    monkeypatch.setattr(fixer, "codex_ready", lambda: "Codex isn't logged in. Run: codex login")

    app.check_codex_on_startup()

    assert capsys.readouterr().err == (
        "Murmur startup warning: Fix it (⌘⌥⇧F) is unavailable. Codex isn't logged in. Run: codex login\n"
    )


# --- Undo last fix and the spoken summary ----------------------------------------


def test_undo_last_fix_puts_the_file_back_and_says_so(tmp_path: Path) -> None:
    from test_fixer_job import git

    git(tmp_path, "init", "-q")
    (tmp_path / "shop.py").write_text("old\n")
    before = fixer.snapshot(tmp_path)
    (tmp_path / "shop.py").write_text("new\n")
    fixer.remember_fix(fixer.LastFix(tmp_path, before, fixer.snapshot(tmp_path), ["shop.py"]))
    said = []

    assert fixer.undo_last_fix(say=said.append)

    assert (tmp_path / "shop.py").read_text() == "old\n"
    assert said == ["Undone. shop.py is back how it was."]
    assert fixer.last_fix() is None  # one undo per fix


def test_undo_with_nothing_to_undo() -> None:
    said = []

    assert not fixer.undo_last_fix(say=said.append)
    assert said == ["There's no fix to undo."]


def test_summary_skips_the_file_list_when_codex_already_named_the_file() -> None:
    settings.set("personality", "Focused")
    answer = fixer.CodexAnswer(True, "Changed total in shop.py to add the tip.", True)

    text = fixer.spoken_fix_summary(["src/shop.py"], answer)

    assert text.count("shop.py") == 1
    assert text.startswith("Fix applied. Changed total in shop.py")


def test_summary_keeps_the_file_list_when_codex_did_not_name_every_file() -> None:
    settings.set("personality", "Focused")
    answer = fixer.CodexAnswer(True, "Fixed the tax maths in shop.py.", False)

    text = fixer.spoken_fix_summary(["shop.py", "tax.py"], answer)

    assert "Changed shop.py and tax.py." in text
