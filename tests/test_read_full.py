"""Read full output: ⌘⌥⇧R, the menu item, and reading every word in Focused style."""

from collections import namedtuple

import pytest

from murmur import app, config, hotkey, personality, settings, voice
from test_hotkey import FAKE_KEYBOARD, FakeKey

letter = namedtuple("KeyCode", "char vk")


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_NAME = None
        app._JOB_CANCELLED.clear()
    app._remember_output(None)
    voice.reset_stop()
    yield
    app._remember_output(None)


@pytest.fixture
def spoken(monkeypatch) -> list:
    said = []
    monkeypatch.setattr(app.voice, "speak", lambda text, mode=None: said.append((text, mode)) or "OpenAI")
    return said


def test_cmd_option_shift_r_reads_and_nothing_else() -> None:
    calls = []
    watcher = hotkey.ShortcutWatcher(
        FAKE_KEYBOARD,
        lambda: calls.append("announce"),
        on_fix=lambda: calls.append("fix"),
        on_read=lambda: calls.append("read"),
    )
    for modifier in (FakeKey.cmd, FakeKey.alt, FakeKey.shift):
        watcher.press(modifier)
    watcher.press(letter("‰", config.READ_KEY_CODE))  # what macOS types for ⌥⇧R

    assert calls == ["read"]


def test_the_three_letter_shortcuts_never_share_a_key() -> None:
    codes = {config.HOTKEY_KEY_CODE, config.FIX_KEY_CODE, config.READ_KEY_CODE}
    assert codes == {46, 3, 15}  # M, F, R


def test_reads_every_word_in_focused_style_even_in_smooth_mode(spoken) -> None:
    settings.set("style", "Friendly")
    app._remember_output("FAILED shop_demo_check.py::test_total\nshop.py:9: AssertionError\nassert 18.40 == 21.60")

    app.read_full_output()

    assert spoken == [
        (
            "FAILED shop_demo_check.py::test_total\nshop.py, line 9: AssertionError\nassert 18.40 == 21.60",
            personality.Mode.BRIEF,
        )
    ]


def test_long_output_is_read_in_pieces_and_stops_when_cancelled(spoken, monkeypatch) -> None:
    monkeypatch.setattr(app.readout, "MAX_PIECE_CHARS", 1200)
    app._remember_output("\n".join(f"line {number} " + "x" * 60 for number in range(100)))
    cancel = app.threading.Event()

    def speak_then_cancel(text, mode=None):
        spoken.append(text)
        cancel.set()  # as if you pressed a shortcut during the first piece
        return "OpenAI"

    monkeypatch.setattr(app.voice, "speak", speak_then_cancel)

    app.read_full_output(cancel)

    assert len(spoken) == 1  # the rest was never read


def test_with_auto_announce_off_it_reads_terminal_now(spoken) -> None:
    settings.set("auto_announce", False)
    app._remember_output("an old output")

    app.read_full_output(read_text=lambda: "shell % make\nbuild ok\nshell % ")

    assert spoken[0][0] == "build ok"


def test_nothing_remembered_reads_terminal(spoken) -> None:
    app.read_full_output(read_text=lambda: "shell % ls\nshop.py\nshell % ")

    assert spoken[0][0] == "shop.py"


def test_empty_output_says_so(spoken) -> None:
    app._remember_output("")

    app.read_full_output()

    assert spoken == [("There's no output to read.", personality.Mode.BRIEF)]


def test_a_terminal_problem_is_spoken(spoken) -> None:
    def broken():
        raise RuntimeError("Terminal.app is not running.")

    app.read_full_output(read_text=broken)

    assert spoken == [("Terminal.app is not running.", personality.Mode.BRIEF)]


def test_announcements_remember_the_cleaned_output(monkeypatch) -> None:
    monkeypatch.setattr(app.voice, "play_soft_sound", lambda *a: "sound")
    monkeypatch.setattr(app.voice, "speak", lambda *a, **k: "OpenAI")

    app.announce_last_output(read_text=lambda: "shell % ls\n\x1b[32mshop.py\x1b[0m\nshell % ")

    assert app.last_output() == "shop.py"


def test_any_shortcut_stops_the_reading(monkeypatch) -> None:
    with app._JOB_LOCK:
        app._JOB_ACTIVE = True
        app._JOB_NAME = "murmur-read-full"

    app.start_announcement(stop_if_running=True)

    assert app._JOB_CANCELLED.is_set()


def test_pressing_r_again_stops_it_instead_of_starting_over() -> None:
    with app._JOB_LOCK:
        app._JOB_ACTIVE = True
        app._JOB_NAME = "murmur-read-full"

    assert not app.start_read_full(stop_if_running=True)
    assert app._JOB_CANCELLED.is_set()


def test_menu_item_sits_under_read_last_output_with_a_spoken_label() -> None:
    menu_app = app.MurmurApp()
    titles = list(menu_app.menu.keys())

    assert titles[titles.index(app.read_last_output_title()) + 1] == "Read everything (⌘⌥⇧R)"
    assert str(menu_app.read_full_item._menuitem.accessibilityLabel()) == (
        "Read everything, the whole output word for word. Shortcut: Command Option Shift R, "
        "or double tap Right Command then R"
    )
