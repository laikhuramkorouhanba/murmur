"""Global keyboard shortcuts for starting or stopping an announcement.

There are two ways in:
- ⌘⌥⇧M (Command + Option + Shift + M)
- tapping the right Option key twice, quickly
"""

import ctypes
import os
import subprocess
import time
from collections.abc import Callable
from typing import Any

from murmur import config


def _load_keyboard():
    from pynput import keyboard

    return keyboard


class ShortcutWatcher:
    """Watch every key press and call on_activate when a Murmur shortcut happens.

    We match the M key by its physical key code, not by its letter. With Option
    held, macOS turns "m" into a different character, so matching the letter
    never worked.
    """

    def __init__(
        self,
        keyboard: Any,
        on_activate: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
        on_first_event: Callable[[], None] | None = None,
    ) -> None:
        self._on_activate = on_activate
        self._clock = clock
        self._on_first_event = on_first_event
        # Left and right versions of a modifier count as the same thing.
        self._modifier_names = {
            keyboard.Key.cmd: "cmd",
            keyboard.Key.cmd_r: "cmd",
            keyboard.Key.alt: "alt",
            keyboard.Key.alt_r: "alt",
            keyboard.Key.shift: "shift",
            keyboard.Key.shift_r: "shift",
            keyboard.Key.ctrl: "ctrl",
            keyboard.Key.ctrl_r: "ctrl",
        }
        self._right_option = keyboard.Key.alt_r
        self._held: set[str] = set()
        # Double-tap memory: when the last clean tap of right Option ended.
        self._right_option_down_alone = False
        self._last_tap_end: float | None = None

    def press(self, key: Any) -> None:
        if self._on_first_event is not None:
            self._on_first_event()
            self._on_first_event = None

        if key == self._right_option:
            self._right_option_down_alone = True
        else:
            # Any other key in between means it is not a double tap.
            self._right_option_down_alone = False
            self._last_tap_end = None

        name = self._modifier_names.get(key)
        if name:
            self._held.add(name)
            return

        if getattr(key, "vk", None) == config.HOTKEY_KEY_CODE and self._held == {"cmd", "alt", "shift"}:
            self._on_activate()

    def release(self, key: Any) -> None:
        name = self._modifier_names.get(key)
        if name:
            self._held.discard(name)

        if key != self._right_option or not self._right_option_down_alone:
            return
        self._right_option_down_alone = False

        now = self._clock()
        if self._last_tap_end is not None and now - self._last_tap_end <= config.DOUBLE_TAP_SECONDS:
            self._last_tap_end = None
            self._on_activate()
        else:
            self._last_tap_end = now


def launching_app_name() -> str:
    """Find the Mac app Murmur was started from, like Terminal or Visual Studio Code.

    macOS gives keyboard permissions to that app, not to Python, so the
    warning has to name it. We walk up the parent processes and keep the
    outermost one that lives inside a ".app" bundle.
    """
    name = None
    pid = os.getppid()
    for _ in range(30):
        if pid <= 1:
            break
        try:
            line = subprocess.run(
                ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            break
        if not line:
            break
        parent, _, command = line.partition(" ")
        if ".app/" in command:
            name = command.split(".app/")[0].rsplit("/", 1)[-1]
        try:
            pid = int(parent)
        except ValueError:
            break
    return name or "the app you started Murmur from"


def missing_permissions() -> list[str]:
    """Ask macOS which keyboard permissions this process is missing."""
    missing = []
    try:
        services = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        services.AXIsProcessTrusted.restype = ctypes.c_bool
        if not services.AXIsProcessTrusted():
            missing.append("Accessibility")
    except OSError:
        pass
    try:
        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOHIDCheckAccess.restype = ctypes.c_uint
        iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint]
        # 1 asks about "listen to key events"; the answer 0 means granted.
        if iokit.IOHIDCheckAccess(1) != 0:
            missing.append("Input Monitoring")
    except (OSError, AttributeError):
        pass
    return missing


def start_hotkey_listener(on_activate: Callable[[], None]) -> Any:
    """Start both shortcuts, or explain exactly which permission is missing."""
    missing = missing_permissions()
    if missing:
        app = launching_app_name()
        raise RuntimeError(
            f"macOS has not given {app} {' and '.join(missing)} permission. Turn on {app} in "
            f"System Settings → Privacy & Security → {' and '.join(missing)}, then quit and "
            f"reopen {app}."
        )

    keyboard = _load_keyboard()
    watcher = ShortcutWatcher(
        keyboard,
        on_activate,
        on_first_event=lambda: print("Murmur hotkey: key events are arriving. Shortcuts are live."),
    )
    listener = keyboard.Listener(on_press=watcher.press, on_release=watcher.release)
    listener.start()
    listener.wait()

    if not getattr(listener, "IS_TRUSTED", True):
        listener.stop()
        app = launching_app_name()
        raise RuntimeError(
            f"The global hotkeys are blocked. Turn on {app} in System Settings → "
            f"Privacy & Security → Input Monitoring and Accessibility, then quit and reopen {app}."
        )
    return listener
