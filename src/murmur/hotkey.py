"""Global keyboard shortcut for starting or stopping an announcement."""

from collections.abc import Callable
from typing import Any

from pynput import keyboard

from murmur import config


def start_hotkey_listener(on_activate: Callable[[], None]) -> Any:
    """Start the configured shortcut or explain which macOS permission is missing."""
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
