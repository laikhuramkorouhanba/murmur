"""Murmur's small macOS menu bar app."""

import sys
import threading
from collections.abc import Callable

from murmur import config, voice
from murmur.cleaner import clean
from murmur.decision import Action, contains_error, decide, speech_failure_summary
from murmur import hotkey, settings, welcome
from murmur.hotkey import start_hotkey_listener
from murmur import personality
from murmur.summariser import summarise
from murmur.watcher import get_terminal_text, last_command_output
import rumps


_JOB_LOCK = threading.Lock()
_JOB_ACTIVE = False
_JOB_CANCELLED = threading.Event()
_HOTKEY_LISTENER = None


def read_last_output_title() -> str:
    """Menu label that always shows the shortcuts that work right now."""
    return f"Read last output ({hotkey.shortcuts_label()})"


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


def _run_test_voice_job() -> None:
    global _JOB_ACTIVE
    try:
        mode = personality.get_mode()
        sentence = personality.sample_sentence()
        print("----- Murmur: test voice -----")
        print(sentence)
        if _JOB_CANCELLED.is_set():
            engine = "none (stopped)"
        else:
            engine = voice.speak(sentence, mode=mode)
        print(f"Voice engine: {engine}")
        print("----- End of Murmur voice test -----")
    finally:
        with _JOB_LOCK:
            _JOB_ACTIVE = False


def _run_welcome_job() -> None:
    global _JOB_ACTIVE
    try:
        # Mark it done straight away, so a quit halfway through never makes it nag again.
        settings.set("welcome_done", True)
        mode = personality.get_mode()
        text = welcome.welcome_text(mode)
        print("----- Murmur: welcome -----")
        print(text)
        engine = "none (stopped)" if _JOB_CANCELLED.is_set() else voice.speak(text, mode=mode)
        print(f"Voice engine: {engine}")
        print("----- End of Murmur welcome -----")
    finally:
        with _JOB_LOCK:
            _JOB_ACTIVE = False


def _start_background_job(
    target: Callable[[], None],
    name: str,
    *,
    stop_if_running: bool = False,
) -> bool:
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
        threading.Thread(target=target, name=name, daemon=True).start()
        return True


def start_announcement(*, stop_if_running: bool = False) -> bool:
    """Start one background announcement, optionally stopping the current one."""
    return _start_background_job(
        _run_announcement_job,
        "murmur-announcement",
        stop_if_running=stop_if_running,
    )


def start_test_voice() -> bool:
    """Speak the selected mode's sample sentence in the background."""
    return _start_background_job(_run_test_voice_job, "murmur-test-voice")


def start_welcome() -> bool:
    """Speak the welcome in the background. The hotkey can stop it like any speech."""
    return _start_background_job(_run_welcome_job, "murmur-welcome")


def welcome_if_first_run() -> bool:
    """Play the welcome only the very first time Murmur starts."""
    if settings.get("welcome_done"):
        return False
    return start_welcome()


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
            f"Murmur hotkey: {hotkey.shortcuts_label()} starts announcements; "
            "press either again to stop speech."
        )
        return True
    except Exception as error:
        print(
            f"Murmur startup warning: hotkeys {config.HOTKEY_LABEL} and "
            f"{hotkey.double_tap_label()} are unavailable. {error}",
            file=sys.stderr,
        )
        return False


def _speak_label(item: rumps.MenuItem, label: str) -> None:
    """Give a menu item the exact words VoiceOver should say for it.

    The visible title can stay short and use symbols like ⌘. VoiceOver gets
    plain words instead, so nothing depends on reading a symbol.
    """
    try:
        item._menuitem.setAccessibilityLabel_(label)
    except AttributeError:
        pass


def _read_last_output_label() -> str:
    return (
        "Read last output. Shortcut: "
        f"{config.HOTKEY_SPOKEN}, or {hotkey.double_tap_label()}."
    )


_PERSONALITY_LABELS = {
    personality.Mode.SMOOTH: "Smooth: relaxed and a little playful",
    personality.Mode.FOCUSED: "Focused: short and neutral",
}


class MurmurApp(rumps.App):
    """The Murmur menu bar item and everything in its menu.

    Every item has plain spoken words for VoiceOver, and each submenu's title
    says the current choice, like "Voice: Marin", so you hear it without opening it.
    """

    def __init__(self) -> None:
        super().__init__("Murmur", quit_button="Quit Murmur")

        self.read_item = rumps.MenuItem(read_last_output_title(), callback=self.read_last_output)

        self.personality_menu = rumps.MenuItem("Personality")
        self.personality_items = {}
        for mode in personality.Mode:
            item = rumps.MenuItem(mode.value, callback=self._personality_chooser(mode))
            _speak_label(item, _PERSONALITY_LABELS[mode])
            self.personality_items[mode] = item
            self.personality_menu.add(item)

        self.voice_menu = rumps.MenuItem("Voice")
        self.voice_items = {}
        for choice, name in config.VOICES.items():
            item = rumps.MenuItem(name, callback=self._voice_chooser(choice))
            self.voice_items[choice] = item
            self.voice_menu.add(item)

        self.tap_menu = rumps.MenuItem("Double-tap key")
        self.tap_items = {}
        for choice, name in config.DOUBLE_TAP_KEYS.items():
            item = rumps.MenuItem(name, callback=self._tap_chooser(choice))
            _speak_label(item, f"{name}: tap it twice to read the last output")
            self.tap_items[choice] = item
            self.tap_menu.add(item)

        self.test_voice_item = rumps.MenuItem("Test voice", callback=self.test_voice)
        _speak_label(self.test_voice_item, "Test voice: speak a sample sentence")
        self.welcome_item = rumps.MenuItem("Replay welcome", callback=self.replay_welcome)
        _speak_label(
            self.welcome_item,
            "Replay welcome: hear the shortcuts and missing permission help again",
        )

        self.menu.update(
            [
                self.read_item,
                None,
                self.personality_menu,
                self.voice_menu,
                self.tap_menu,
                None,
                self.test_voice_item,
                self.welcome_item,
            ]
        )
        self._show_personality_choice()
        self._show_voice_choice()
        self._show_tap_choice()
        rumps.events.before_start.register(self._label_menu_bar_item)

    def _label_menu_bar_item(self) -> None:
        # The menu bar item itself only exists once the app is starting up.
        try:
            self._nsapp.nsstatusitem.button().setAccessibilityLabel_(
                "Murmur, spoken terminal results"
            )
        except AttributeError:
            pass

    @staticmethod
    def _show_submenu_choice(menu: rumps.MenuItem, heading: str, choice: str) -> None:
        menu.title = f"{heading}: {choice}"
        _speak_label(menu, f"{heading}, currently {choice}")

    def _personality_chooser(self, mode: personality.Mode) -> Callable[[object], None]:
        def choose(_sender: object) -> None:
            self._choose_personality(mode)

        return choose

    def _choose_personality(self, mode: personality.Mode) -> None:
        personality.set_mode(mode)
        self._show_personality_choice()

    def _show_personality_choice(self) -> None:
        current = personality.get_mode()
        for mode, item in self.personality_items.items():
            item.state = int(mode is current)
        self._show_submenu_choice(self.personality_menu, "Personality", current.value)

    def choose_smooth(self, _sender: object) -> None:
        self._choose_personality(personality.Mode.SMOOTH)

    def choose_focused(self, _sender: object) -> None:
        self._choose_personality(personality.Mode.FOCUSED)

    def _voice_chooser(self, choice: str) -> Callable[[object], None]:
        def choose(_sender: object) -> None:
            self.choose_voice(choice)

        return choose

    def choose_voice(self, choice: str) -> None:
        settings.set("voice", choice)
        self._show_voice_choice()
        print(f"Murmur voice: now {config.VOICES[choice]}.")

    def _show_voice_choice(self) -> None:
        current = voice.current_voice()
        for choice, item in self.voice_items.items():
            item.state = int(choice == current)
        self._show_submenu_choice(self.voice_menu, "Voice", config.VOICES[current])

    def _tap_chooser(self, choice: str) -> Callable[[object], None]:
        def choose(_sender: object) -> None:
            self.choose_double_tap(choice)

        return choose

    def choose_double_tap(self, choice: str) -> None:
        settings.set("double_tap_key", choice)
        self._show_tap_choice()
        print(f"Murmur hotkey: now {hotkey.shortcuts_label()}.")

    def _show_tap_choice(self) -> None:
        current = hotkey.double_tap_choice()
        for choice, item in self.tap_items.items():
            item.state = int(choice == current)
        self._show_submenu_choice(self.tap_menu, "Double-tap key", config.DOUBLE_TAP_KEYS[current])
        self.read_item.title = read_last_output_title()
        _speak_label(self.read_item, _read_last_output_label())

    def read_last_output(self, _sender: object) -> None:
        start_announcement()

    def test_voice(self, _sender: object) -> None:
        start_test_voice()

    def replay_welcome(self, _sender: object) -> None:
        start_welcome()


def main() -> None:
    for warning in config.startup_warnings():
        print(warning, file=sys.stderr)
    check_terminal_on_startup()
    start_hotkey()
    welcome_if_first_run()
    MurmurApp().run()


if __name__ == "__main__":
    main()
