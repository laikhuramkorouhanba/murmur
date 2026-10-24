"""Global keyboard shortcut for starting or stopping an announcement."""

from collections.abc import Callable
from typing import Any

from murmur import config


def _load_keyboard():
    from pynput import keyboard

    return keyboard


def start_hotkey_listener(on_activate: Callable[[], None]) -> Any:
    """Start the configured shortcut or explain which macOS permission is missing."""
    keyboard = _load_keyboard()
    hotkey = keyboard.HotKey(keyboard.HotKey.parse(config.HOTKEY), on_activate)
    listener = keyboard.Listener(on_press=hotkey.press, on_release=hotkey.release)
    listener.start()
    listener.wait()

    if not getattr(listener, "IS_TRUSTED", True):
        listener.stop()
        raise RuntimeError(
            "The global hotkey is blocked. Enable Terminal in System Settings → "
            "Privacy & Security → Input Monitoring and Accessibility, then quit and "
            "reopen Terminal."
        )
    return listener
