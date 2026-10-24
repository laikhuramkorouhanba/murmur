"""Murmur's small macOS menu bar app."""

import sys
import threading
from collections.abc import Callable

from murmur import config, voice
from murmur.cleaner import clean
from murmur.decision import Action, contains_error, decide, speech_failure_summary
from murmur.hotkey import start_hotkey_listener
from murmur import personality
from murmur.summariser import summarise
from murmur.watcher import get_terminal_text, last_command_output
import rumps


_JOB_LOCK = threading.Lock()
_JOB_ACTIVE = False
_JOB_CANCELLED = threading.Event()
_HOTKEY_LISTENER = None
READ_LAST_OUTPUT_MENU_TITLE = f"Read last output ({config.HOTKEY_LABEL})"


def print_last_output() -> None:
    """Print the cleaned output of Terminal's most recent command."""
    print("----- Murmur: last command output -----")
    try:
        history = get_terminal_text()
        output = clean(last_command_output(history))
        print(output or "(No output found for the last command.)")
    except RuntimeError as error:
        print(error)
    print("----- End of Murmur output -----")


def announce_last_output(
    cancel_event: threading.Event | None = None,
    *,
    read_text: Callable[[], str] | None = None,
) -> None:
    """Read, clean, decide, summarise if needed, and speak on a worker thread."""
    cancelled = cancel_event or threading.Event()
    reader = read_text or get_terminal_text
    print("----- Murmur: cleaned terminal output -----")
    try:
        cleaned = clean(last_command_output(reader()))
    except RuntimeError as error:
        print(error)
        print("Voice engine: none (no speech)")
        print("----- End of Murmur announcement -----")
        return

    print(cleaned or "(No output found for the last command.)")
    failed = contains_error(cleaned)
    personality.record_result(failed)
    if cancelled.is_set():
        print("Voice engine: none (stopped)")
        print("----- End of Murmur announcement -----")
        return

    action = decide(cleaned)
    if action is Action.SOFT_SOUND:
        print("----- Murmur: spoken text -----")
        print("(none; playing the soft success sound)")
        engine = voice.play_soft_sound() if not cancelled.is_set() else "none (stopped)"
        print(f"Voice engine: {engine}")
        print("----- End of Murmur announcement -----")
        return

    if action is Action.SPEAK_EXACTLY:
        spoken_text = cleaned
    else:
        summary = summarise(cleaned)
        if failed:
            summary = speech_failure_summary(cleaned, summary)
        spoken_text = personality.style_summary(summary, failed=failed)
    if cancelled.is_set():
        print("Voice engine: none (stopped)")
        print("----- End of Murmur announcement -----")
        return

    print("----- Murmur: spoken text -----")
    print(spoken_text)
    engine = voice.speak(spoken_text, mode=personality.get_mode())
    print(f"Voice engine: {engine}")
    print("----- End of Murmur announcement -----")


def _run_announcement_job() -> None:
    global _JOB_ACTIVE
    try:
        announce_last_output(_JOB_CANCELLED)
    finally:
        with _JOB_LOCK:
            _JOB_ACTIVE = False


def start_announcement(*, stop_if_running: bool = False) -> bool:
    """Start one background announcement, optionally stopping the current one."""
    global _JOB_ACTIVE
    with _JOB_LOCK:
        if _JOB_ACTIVE:
            if stop_if_running:
                _JOB_CANCELLED.set()
                voice.stop_speech()
            return False

        _JOB_ACTIVE = True
        _JOB_CANCELLED.clear()
        voice.reset_stop()
        threading.Thread(
            target=_run_announcement_job,
            name="murmur-announcement",
            daemon=True,
        ).start()
        return True


def check_terminal_on_startup() -> None:
    """Print a helpful warning if Terminal cannot be read yet."""
    try:
        get_terminal_text()
    except RuntimeError as error:
        print(f"Murmur startup warning: {error}", file=sys.stderr)


def start_hotkey() -> bool:
    global _HOTKEY_LISTENER
    try:
        _HOTKEY_LISTENER = start_hotkey_listener(
            lambda: start_announcement(stop_if_running=True)
        )
        print(
            f"Murmur hotkey: {config.HOTKEY_LABEL} starts announcements; "
            "press it again to stop speech."
        )
        return True
    except Exception as error:
        print(
            f"Murmur startup warning: hotkey {config.HOTKEY_LABEL} is unavailable. {error}",
            file=sys.stderr,
        )
        return False


class MurmurApp(rumps.App):
    """Show Murmur in the menu bar and provide a way to quit."""

    def __init__(self) -> None:
        super().__init__("Murmur", quit_button="Quit")
        smooth = rumps.MenuItem("Smooth", callback=self.choose_smooth)
        focused = rumps.MenuItem("Focused", callback=self.choose_focused)
        smooth.state = 1
        personality_menu = rumps.MenuItem("Personality")
        personality_menu.update([smooth, focused])
        self.menu.update([personality_menu])

    def _choose_personality(self, mode: personality.Mode) -> None:
        personality.set_mode(mode)
        self.menu["Personality"]["Smooth"].state = int(mode is personality.Mode.SMOOTH)
        self.menu["Personality"]["Focused"].state = int(mode is personality.Mode.FOCUSED)

    def choose_smooth(self, _sender: object) -> None:
        self._choose_personality(personality.Mode.SMOOTH)

    def choose_focused(self, _sender: object) -> None:
        self._choose_personality(personality.Mode.FOCUSED)

    @rumps.clicked(READ_LAST_OUTPUT_MENU_TITLE)
    def read_last_output(self, _sender: object) -> None:
        start_announcement()


def main() -> None:
    for warning in config.startup_warnings():
        print(warning, file=sys.stderr)
    check_terminal_on_startup()
    start_hotkey()
    MurmurApp().run()


if __name__ == "__main__":
    main()
