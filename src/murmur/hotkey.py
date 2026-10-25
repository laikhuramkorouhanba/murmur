"""Global keyboard shortcuts.

- Double tap one right-hand modifier key (Right Command by default): Murmur says
  "Murmur" and listens for one key for about 3 seconds. Space repeats, R reads the
  full output, F fixes, U undoes, Escape cancels. That key never reaches Terminal.
- Backups: ⌘⌥⇧M reads the last output, ⌘⌥⇧F fixes, ⌘⌥⇧R reads the full output.
- Control on its own stops speech, like VoiceOver.

How the key is kept from Terminal: macOS lets an app put an "event tap" in front of
the keyboard. Murmur's normal tap only listens. A second tap, which can swallow
keys, is made once at startup but stays switched off; it's switched on only while
the layer waits for its key. So the rest of the time, your typing never waits for
Murmur, and a slow moment in Murmur can never hold up your keyboard.
"""

import ctypes
import os
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from murmur import config, settings


# Which pynput key each "Double-tap key" menu choice means.
_TAP_KEY_NAMES = {
    "right_command": "cmd_r",
    "right_option": "alt_r",
    "right_shift": "shift_r",
}


def double_tap_choice() -> str:
    """The saved double-tap key, or the default if the saved one is unknown."""
    choice = settings.get("double_tap_key")
    return choice if choice in config.DOUBLE_TAP_KEYS else settings.DEFAULTS["double_tap_key"]


def double_tap_label() -> str:
    """Human words for the current double tap, like "Right Command twice"."""
    return f"{config.DOUBLE_TAP_KEYS[double_tap_choice()]} twice"


def shortcuts_label() -> str:
    """The shortcuts for reading the last output, in one short phrase for messages."""
    return f"{config.HOTKEY_LABEL}, or {double_tap_label()} then Space"


class KeyLayer:
    """The Murmur key layer: after a double tap, the next key is a Murmur command.

    key_down and key_up return True when the key belongs to Murmur and must be
    swallowed. Any other key closes the layer and goes through as normal, so you
    can never get stuck in it. With no key for KEY_LAYER_SECONDS, it closes by itself.
    """

    def __init__(
        self,
        on_command: Callable[[str], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        seconds: float = config.KEY_LAYER_SECONDS,
        on_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._on_command = on_command
        self._clock = clock
        self._seconds = seconds
        self._on_change = on_change
        self._lock = threading.Lock()
        self._until: float | None = None
        self._held: set[int] = set()  # Murmur keys still down, whose key-up we must swallow too

    def open(self) -> None:
        with self._lock:
            self._until = self._clock() + self._seconds
        self._changed()
        # When the time is up, check again, so the swallowing tap goes off even if no key comes.
        timer = threading.Timer(self._seconds + 0.05, self._changed)
        timer.daemon = True
        timer.start()

    def close(self) -> None:
        with self._lock:
            self._until = None
        self._changed()

    def is_open(self) -> bool:
        with self._lock:
            return self._open_now()

    def _open_now(self) -> bool:
        if self._until is not None and self._clock() > self._until:
            self._until = None  # timed out
        return self._until is not None

    def needs_swallowing(self) -> bool:
        """True while the swallowing tap must stay on: the layer is open, or a Murmur key is still down."""
        with self._lock:
            return self._open_now() or bool(self._held)

    def key_down(self, code: int) -> bool:
        with self._lock:
            if code in self._held:
                return True  # the key is held down and repeating: keep it from Terminal too
            if not self._open_now():
                return False
            self._until = None  # one key per double tap
            command = config.KEY_LAYER_KEYS.get(code)
            if command is not None:
                self._held.add(code)
        if command is not None:
            self._on_command(command)
        self._changed()
        return command is not None

    def key_up(self, code: int) -> bool:
        with self._lock:
            swallowed = code in self._held
            self._held.discard(code)
        self._changed()
        return swallowed

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change(self.needs_swallowing())


class KeySwallower:
    """The tap that keeps the layer's key from Terminal. Off unless the layer needs it."""

    def __init__(self, layer: KeyLayer) -> None:
        import Quartz  # comes with pynput on macOS

        self._quartz = Quartz
        self._layer = layer
        self._wanted = False
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,  # may swallow keys (needs Accessibility)
            mask,
            self._handle,
            None,
        )
        if self._tap is None:
            raise RuntimeError("macOS didn't allow a key tap that can keep keys from Terminal.")
        Quartz.CGEventTapEnable(self._tap, False)  # off until a double tap
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), name="murmur-key-layer", daemon=True).start()
        ready.wait(2)

    def _run(self, ready: threading.Event) -> None:
        quartz = self._quartz
        source = quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        quartz.CFRunLoopAddSource(quartz.CFRunLoopGetCurrent(), source, quartz.kCFRunLoopDefaultMode)
        ready.set()
        quartz.CFRunLoopRun()

    def set_enabled(self, on: bool) -> None:
        self._wanted = on
        self._quartz.CGEventTapEnable(self._tap, on)

    def _handle(self, _proxy, event_type, event, _refcon):
        quartz = self._quartz
        if event_type == quartz.kCGEventTapDisabledByTimeout:
            # macOS switches a tap off if it ever answers too slowly. Switch it back on if needed.
            if self._wanted:
                quartz.CGEventTapEnable(self._tap, True)
            return event
        if event_type not in (quartz.kCGEventKeyDown, quartz.kCGEventKeyUp):
            return event
        code = quartz.CGEventGetIntegerValueField(event, quartz.kCGKeyboardEventKeycode)
        if event_type == quartz.kCGEventKeyDown:
            swallow = self._layer.key_down(code)
        else:
            swallow = self._layer.key_up(code)
        return None if swallow else event


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
        tap_choice: Callable[[], str] = double_tap_choice,
        on_fix: Callable[[], None] | None = None,
        on_read: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_double_tap: Callable[[], None] | None = None,
    ) -> None:
        self._on_activate = on_activate
        self._on_stop = on_stop
        # The double tap opens the Murmur key layer. Without one, it reads the last output.
        self._on_double_tap = on_double_tap or on_activate
        self._on_fix = on_fix
        self._on_read = on_read
        self._keyboard = keyboard
        self._tap_choice = tap_choice
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
        self._held: set[str] = set()
        # Double-tap memory: is the tap key down on its own, and when did the last clean tap end?
        self._tap_down_alone = False
        self._last_tap_end: float | None = None

    def _tap_key(self) -> Any:
        # Asked fresh every time, so a new menu choice works straight away.
        return getattr(self._keyboard.Key, _TAP_KEY_NAMES[self._tap_choice()])

    def press(self, key: Any) -> None:
        if self._on_first_event is not None:
            self._on_first_event()
            self._on_first_event = None

        if key == self._tap_key():
            self._tap_down_alone = True
        else:
            # Any other key in between means it is not a double tap.
            self._tap_down_alone = False
            self._last_tap_end = None

        name = self._modifier_names.get(key)
        if name:
            # Control pressed on its own stops speech straight away, the way it does in VoiceOver.
            if name == "ctrl" and not self._held and self._on_stop is not None:
                self._on_stop()
            self._held.add(name)
            return

        if self._held != {"cmd", "alt", "shift"}:
            return
        code = getattr(key, "vk", None)
        if code == config.HOTKEY_KEY_CODE:
            self._on_activate()
        elif code == config.FIX_KEY_CODE and self._on_fix is not None:
            self._on_fix()
        elif code == config.READ_KEY_CODE and self._on_read is not None:
            self._on_read()

    def release(self, key: Any) -> None:
        name = self._modifier_names.get(key)
        if name:
            self._held.discard(name)

        if key != self._tap_key() or not self._tap_down_alone:
            return
        self._tap_down_alone = False

        now = self._clock()
        if self._last_tap_end is not None and now - self._last_tap_end <= config.DOUBLE_TAP_SECONDS:
            self._last_tap_end = None
            self._on_double_tap()
        else:
            self._last_tap_end = now


def launching_app_name() -> str:
    """Find the Mac app Murmur was started from, like Terminal or Visual Studio Code.

    macOS gives keyboard permissions to that app, not to Python, so the
    warning has to name it. We walk up the parent processes and keep the
    outermost one that lives inside a ".app" bundle.
    """
    if config.is_packaged():
        return "Murmur"  # the built app asks for permissions for itself
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


def start_hotkey_listener(
    on_activate: Callable[[], None],
    on_fix: Callable[[], None] | None = None,
    on_read: Callable[[], None] | None = None,
    on_stop: Callable[[], None] | None = None,
    on_layer_open: Callable[[], None] | None = None,
    on_layer_command: Callable[[str], None] | None = None,
) -> Any:
    """Start the shortcuts, or explain exactly which permission is missing.

    With on_layer_open and on_layer_command, the double tap opens the Murmur key
    layer. If macOS won't allow the key-swallowing tap, the double tap reads the
    last output instead (the old way), and a startup warning says why.
    """
    missing = missing_permissions()
    if missing:
        app = launching_app_name()
        raise RuntimeError(
            f"macOS has not given {app} {' and '.join(missing)} permission. Turn on {app} in "
            f"System Settings → Privacy & Security → {' and '.join(missing)}, then quit and "
            f"reopen {app}."
        )

    keyboard = _load_keyboard()
    on_double_tap = None
    if on_layer_open is not None and on_layer_command is not None:
        try:
            swallower: KeySwallower | None = None
            layer = KeyLayer(on_layer_command, on_change=lambda on: swallower and swallower.set_enabled(on))
            swallower = KeySwallower(layer)

            def on_double_tap() -> None:
                layer.open()
                on_layer_open()

        except Exception as error:
            print(f"Murmur startup warning: the Murmur key layer is off ({error}). "
                  "Double tap reads the last output instead.")
    watcher = ShortcutWatcher(
        keyboard,
        on_activate,
        on_fix=on_fix,
        on_read=on_read,
        on_stop=on_stop,
        on_double_tap=on_double_tap,
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
