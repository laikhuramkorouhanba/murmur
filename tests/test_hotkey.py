from collections import namedtuple
from enum import Enum
from types import SimpleNamespace

import pytest

from murmur import config, hotkey


class FakeKey(Enum):
    cmd = "cmd"
    cmd_r = "cmd_r"
    alt = "alt"
    alt_r = "alt_r"
    shift = "shift"
    shift_r = "shift_r"
    ctrl = "ctrl"
    ctrl_r = "ctrl_r"


FAKE_KEYBOARD = SimpleNamespace(Key=FakeKey)


# A letter key the way pynput reports it: a character plus a key code.
letter = namedtuple("KeyCode", "char vk")


M_WITH_OPTION = letter("Â", config.HOTKEY_KEY_CODE)  # what macOS reports for ⌥⇧M


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_watcher():
    fired = []
    clock = FakeClock()
    watcher = hotkey.ShortcutWatcher(FAKE_KEYBOARD, lambda: fired.append(1), clock=clock)
    return watcher, fired, clock


def tap(watcher, key, clock=None, hold=0.05):
    watcher.press(key)
    if clock:
        clock.now += hold
    watcher.release(key)


def test_cmd_option_shift_m_fires_even_when_option_changes_the_letter() -> None:
    watcher, fired, _ = make_watcher()

    for key in (FakeKey.cmd, FakeKey.alt, FakeKey.shift):
        watcher.press(key)
    tap(watcher, M_WITH_OPTION)

    assert fired == [1]


def test_m_without_all_three_modifiers_does_nothing() -> None:
    watcher, fired, _ = make_watcher()

    watcher.press(FakeKey.cmd)
    watcher.press(FakeKey.shift)
    tap(watcher, letter("M", config.HOTKEY_KEY_CODE))

    assert fired == []


def test_double_tap_right_command_fires_by_default() -> None:
    watcher, fired, clock = make_watcher()

    tap(watcher, FakeKey.cmd_r, clock)
    clock.now += 0.2
    tap(watcher, FakeKey.cmd_r, clock)

    assert fired == [1]


def test_slow_double_tap_does_not_fire() -> None:
    watcher, fired, clock = make_watcher()

    tap(watcher, FakeKey.cmd_r, clock)
    clock.now += 0.6
    tap(watcher, FakeKey.cmd_r, clock)

    assert fired == []


def test_another_key_between_taps_cancels_the_double_tap() -> None:
    watcher, fired, clock = make_watcher()

    tap(watcher, FakeKey.cmd_r, clock)
    tap(watcher, letter("a", 0), clock)
    tap(watcher, FakeKey.cmd_r, clock)

    assert fired == []


def test_holding_the_tap_key_for_a_shortcut_is_not_a_tap() -> None:
    watcher, fired, clock = make_watcher()

    tap(watcher, FakeKey.cmd_r, clock)
    watcher.press(FakeKey.cmd_r)
    tap(watcher, letter("é", 14), clock)
    watcher.release(FakeKey.cmd_r)

    assert fired == []


def test_left_command_double_tap_does_not_fire() -> None:
    watcher, fired, clock = make_watcher()

    tap(watcher, FakeKey.cmd, clock)
    tap(watcher, FakeKey.cmd, clock)

    assert fired == []


def test_three_quick_taps_fire_only_once() -> None:
    watcher, fired, clock = make_watcher()

    for _ in range(3):
        tap(watcher, FakeKey.cmd_r, clock)
        clock.now += 0.1

    assert fired == [1]


def test_the_menu_choice_changes_the_tap_key_straight_away() -> None:
    watcher, fired, clock = make_watcher()

    hotkey.settings.set("double_tap_key", "right_shift")
    tap(watcher, FakeKey.cmd_r, clock)
    tap(watcher, FakeKey.cmd_r, clock)
    assert fired == []

    tap(watcher, FakeKey.shift_r, clock)
    tap(watcher, FakeKey.shift_r, clock)
    assert fired == [1]


def test_labels_follow_the_saved_choice() -> None:
    assert hotkey.shortcuts_label() == "⌘⌥⇧M or Right Command twice"

    hotkey.settings.set("double_tap_key", "right_option")
    assert hotkey.double_tap_label() == "Right Option twice"

    hotkey.settings.set("double_tap_key", "something_else")
    assert hotkey.double_tap_label() == "Right Command twice"


def test_first_key_event_is_reported_once() -> None:
    seen = []
    watcher = hotkey.ShortcutWatcher(FAKE_KEYBOARD, lambda: None, on_first_event=lambda: seen.append(1))

    tap(watcher, letter("a", 0))
    tap(watcher, letter("b", 11))

    assert seen == [1]


class FakeListener:
    def __init__(self, *, on_press, on_release, trusted=True):
        self.on_press = on_press
        self.on_release = on_release
        self.IS_TRUSTED = trusted
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def wait(self):
        pass

    def stop(self):
        self.stopped = True


def install_fake_keyboard(monkeypatch, *, trusted=True, missing=()):
    listeners = []

    def listener_factory(**kwargs):
        listener = FakeListener(**kwargs, trusted=trusted)
        listeners.append(listener)
        return listener

    monkeypatch.setattr(
        hotkey,
        "_load_keyboard",
        lambda: SimpleNamespace(Key=FakeKey, Listener=listener_factory),
    )
    monkeypatch.setattr(hotkey, "missing_permissions", lambda: list(missing))
    monkeypatch.setattr(hotkey, "launching_app_name", lambda: "Visual Studio Code")
    return listeners


def test_listener_feeds_every_key_into_the_shortcut_watcher(monkeypatch) -> None:
    listeners = install_fake_keyboard(monkeypatch)
    fired = []

    listener = hotkey.start_hotkey_listener(lambda: fired.append(1))
    for key in (FakeKey.cmd, FakeKey.alt, FakeKey.shift):
        listener.on_press(key)
    listener.on_press(M_WITH_OPTION)

    assert listener is listeners[0] and listener.started
    assert fired == [1]


def test_missing_permission_names_the_app_that_needs_it(monkeypatch) -> None:
    listeners = install_fake_keyboard(monkeypatch, missing=["Input Monitoring"])

    with pytest.raises(RuntimeError) as error:
        hotkey.start_hotkey_listener(lambda: None)

    assert str(error.value) == (
        "macOS has not given Visual Studio Code Input Monitoring permission. Turn on "
        "Visual Studio Code in System Settings → Privacy & Security → Input Monitoring, "
        "then quit and reopen Visual Studio Code."
    )
    assert listeners == []


def test_untrusted_listener_is_stopped_and_names_the_app(monkeypatch) -> None:
    listeners = install_fake_keyboard(monkeypatch, trusted=False)

    with pytest.raises(RuntimeError, match="Turn on Visual Studio Code"):
        hotkey.start_hotkey_listener(lambda: None)

    assert listeners[0].stopped


def test_launching_app_name_finds_the_outer_app(monkeypatch) -> None:
    chain = {
        500: "400 /bin/zsh",
        400: "300 /Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper.app/Contents/MacOS/Code Helper",
        300: "1 /Applications/Visual Studio Code.app/Contents/MacOS/Electron",
    }
    monkeypatch.setattr(hotkey.os, "getppid", lambda: 500)
    monkeypatch.setattr(
        hotkey.subprocess,
        "run",
        lambda command, **_kwargs: SimpleNamespace(stdout=chain[int(command[-1])]),
    )

    assert hotkey.launching_app_name() == "Visual Studio Code"
