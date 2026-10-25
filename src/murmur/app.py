"""Murmur's small macOS menu bar app."""

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from murmur import config, voice
from murmur.cleaner import clean
from murmur.decision import (
    AutoAction,
    auto_action,
    auto_detail,
    brief_failure,
    contains_error,
    has_assertion_cause,
    has_exact_cause,
    is_short_output,
    line_count_line,
    speech_failure_summary,
)
from murmur import captions, demo, fixer, hotkey, keychain, packaged, readout, settings, watcher, welcome
from murmur.hotkey import start_hotkey_listener
from murmur.monitor import AutoAnnouncer, FinishedCommand
from murmur import personality
from murmur.summariser import summarise
from murmur.watcher import get_tab_text, get_terminal_text, last_command_output
import rumps


_JOB_LOCK = threading.Lock()
_JOB_ACTIVE = False
_JOB_NAME: str | None = None  # which job is running, like "murmur-fix"
_JOB_CANCELLED = threading.Event()
_HOTKEY_LISTENER = None
_LAST_OUTPUT_LOCK = threading.Lock()
_LAST_OUTPUT: str | None = None  # the cleaned output of the last command Murmur read
_LAST_SPOKEN: str | None = None  # the last announcement, for "repeat"
_AUTO_ANNOUNCER: AutoAnnouncer | None = None


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


def _lookup_front_tty() -> str | None:
    try:
        snapshot = watcher.tab_snapshot()
    except RuntimeError:
        return None
    return snapshot.tty if snapshot else None


def _lookup_folder(tty: str) -> str | None:
    return watcher.tab_folder(tty)


def _remember_result(
    history: str, cleaned: str, failed: bool, tty: str | None, folder: str | None = None
) -> None:
    """Keep the details of a failure for Fix it, or forget it once that same command passes."""
    if not failed:
        _forget_failure_if_rerun_passed(history)
        return
    if folder is None:  # Demo mode already knows its folder; otherwise ask the tab
        tty = tty or _lookup_front_tty()
        # Look up the folder now, while the shell is still sitting in it.
        folder = _lookup_folder(tty) if tty else None
    fixer.remember_failure(
        fixer.LastFailure(
            tty=tty,
            folder=folder,
            command=watcher.last_command_line(history),
            output=cleaned,
        )
    )


def _end(engine: str) -> None:
    print(f"Voice engine: {engine}")
    print("----- End of Murmur announcement -----")


def failure_words(cleaned: str) -> str:
    """What to say about a failure, in the chosen style."""
    if personality.get_mode() is personality.Mode.BRIEF:
        # VoiceOver also drives braille displays, so it gets the real file names.
        exact = voice.engine() == "voiceover"
        # Brief names the exact cause from the output; only a puzzle needs the summary request.
        summary = "" if has_exact_cause(cleaned) else summarise(cleaned)
        return brief_failure(cleaned, summary, exact_names=exact)
    if has_assertion_cause(cleaned):
        # We can say "it expected X but got Y" ourselves, so skip the summary request:
        # its answer would be thrown away, and it costs one to two seconds.
        facts = speech_failure_summary(cleaned, "")
    else:
        facts = speech_failure_summary(cleaned, summarise(cleaned))
    return personality.style_summary(facts, failed=True)


def speak_output(cleaned: str, cancelled: threading.Event) -> str:
    """Read output word for word (paths spelled out clearly), in pieces so a stop works quickly."""
    engine = "none (stopped)"
    for piece in readout.pieces(readout.speakable(cleaned)):
        if cancelled.is_set():
            return "none (stopped)"
        # Always Brief: no openers, no jokes, just the words.
        engine = voice.speak(piece, mode=personality.Mode.BRIEF)
        _remember_spoken(piece)
    return engine


def _forget_failure_if_rerun_passed(history: str) -> None:
    """A passing rerun of the failed command means it's fixed. Anything else (ls, cat, git
    status while you look around) keeps the failure, so Fix it still knows what to fix."""
    failure = fixer.last_failure()
    if failure is not None and failure.command and failure.command == watcher.last_command_line(history):
        fixer.forget_failure()


def announce_last_output(
    cancel_event: threading.Event | None = None,
    *,
    read_text: Callable[[], str] | None = None,
    tty: str | None = None,
    folder: str | None = None,
) -> None:
    """Read, clean, decide, and speak on a worker thread (the hotkey, and errors from auto-announce).

    - An error: the fail sound, then "Failed. shop.py, line 9. <exact cause>" (Brief).
    - A success: the pass sound, then short output word for word, or "Done, 42 lines."
    """
    cancelled = cancel_event or threading.Event()
    reader = read_text or get_terminal_text
    print("----- Murmur: cleaned terminal output -----")
    try:
        history = reader()
    except RuntimeError as error:
        print(error)
        print("Voice engine: none (no speech)")
        print("----- End of Murmur announcement -----")
        return
    cleaned = clean(last_command_output(history))
    _remember_output(cleaned)

    print(cleaned or "(No output found for the last command.)")
    failed = contains_error(cleaned)
    personality.record_result(failed)
    _remember_result(history, cleaned, failed, tty, folder)
    if cancelled.is_set():
        return _end("none (stopped)")

    if not failed:
        print("----- Murmur: spoken text -----")
        engine = voice.play_pass_sound()
        if not cleaned.strip():
            print("(no output; just the pass sound)")
        elif is_short_output(cleaned):
            print("(short output, read word for word)")
            engine = speak_output(cleaned, cancelled)
        else:
            line = personality.style_summary(line_count_line(cleaned), failed=False)
            print(line)
            engine = _say_line(line, cancelled)
        return _end(engine)

    spoken_text = failure_words(cleaned)
    if cancelled.is_set():
        return _end("none (stopped)")
    print("----- Murmur: spoken text -----")
    print(spoken_text)
    voice.play_fail_sound()
    _end(_say_line(spoken_text, cancelled))


def _say_line(text: str, cancelled: threading.Event) -> str:
    if cancelled.is_set():
        return "none (stopped)"
    _remember_spoken(text)
    return voice.speak(text, mode=personality.get_mode())


def announce_finished_command(
    finished: FinishedCommand,
    cancel_event: threading.Event | None = None,
    *,
    read_text: Callable[[], str] | None = None,
) -> None:
    """Auto-announce one finished command, following the table in decision.auto_action."""
    cancelled = cancel_event or threading.Event()
    reader = read_text or (lambda: get_tab_text(finished.tty))
    try:
        history = reader()
    except RuntimeError as error:
        print(f"Murmur auto-announce: {error}")
        return

    cleaned = clean(last_command_output(history))
    _remember_output(cleaned)
    action = auto_action(cleaned, finished.seconds, auto_detail())
    if action is AutoAction.ANNOUNCE:
        # Errors get exactly what the hotkey gives, reusing the text we already read.
        announce_last_output(cancelled, read_text=lambda: history, tty=finished.tty)
        return

    personality.record_result(False)
    _forget_failure_if_rerun_passed(history)
    print(f"----- Murmur: command finished after about {finished.seconds:.0f} s -----")
    if action is AutoAction.SILENT:
        print("(nothing to say; staying silent)")
        engine = "none (silent)"
    elif cancelled.is_set():
        engine = "none (stopped)"
    else:
        engine = voice.play_pass_sound()
        if action is AutoAction.READ_OUTPUT:
            print(cleaned)
            engine = speak_output(cleaned, cancelled)
        elif action is AutoAction.LINE_COUNT:
            line = personality.style_summary(line_count_line(cleaned), failed=False)
            print(line)
            engine = _say_line(line, cancelled)
        elif action is AutoAction.DONE_LINE:
            line = personality.done_line()
            print(line)
            engine = _say_line(line, cancelled)
        else:
            print("(success; just the pass sound)")
    print(f"Voice engine: {engine}")
    print("----- End of Murmur auto-announce -----")


def _remember_spoken(text: str) -> None:
    """Keep the last thing Murmur announced, so Space in the Murmur key layer can repeat it."""
    global _LAST_SPOKEN
    with _LAST_OUTPUT_LOCK:
        _LAST_SPOKEN = text


def last_spoken() -> str | None:
    with _LAST_OUTPUT_LOCK:
        return _LAST_SPOKEN


def _remember_output(cleaned: str) -> None:
    global _LAST_OUTPUT
    with _LAST_OUTPUT_LOCK:
        _LAST_OUTPUT = cleaned


def last_output() -> str | None:
    with _LAST_OUTPUT_LOCK:
        return _LAST_OUTPUT


def read_full_output(
    cancel_event: threading.Event | None = None,
    *,
    read_text: Callable[[], str] | None = None,
) -> None:
    """Read the last command's whole cleaned output aloud, word for word, in Brief style.

    With auto-announce on, that's the last command Murmur saw finish (in any tab).
    Otherwise we read the front Terminal tab now, so it's never out of date.
    """
    cancelled = cancel_event or threading.Event()
    print("----- Murmur: full output -----")
    text = last_output() if settings.get("auto_announce") else None
    if text is None:
        try:
            text = clean(last_command_output((read_text or get_terminal_text)()))
        except RuntimeError as error:
            text = None
            print(error)
            voice.speak(str(error), mode=personality.Mode.BRIEF)
    if text is not None:
        spoken = readout.speakable(text) or "There's no output to read."
        print(spoken)
        engine = "none (stopped)"
        for piece in readout.pieces(spoken):
            if cancelled.is_set():
                engine = "none (stopped)"
                break
            # Always Brief: no openers, no jokes, just the words.
            engine = voice.speak(piece, mode=personality.Mode.BRIEF)
        print(f"Voice engine: {engine}")
    print("----- End of Murmur full output -----")


def _run_read_full_job() -> None:
    global _JOB_ACTIVE
    try:
        read_full_output(_JOB_CANCELLED)
    finally:
        with _JOB_LOCK:
            _JOB_ACTIVE = False


def start_read_full(*, stop_if_running: bool = False) -> bool:
    """Read the full output in the background. Any Murmur shortcut stops it."""
    return _start_background_job(_run_read_full_job, "murmur-read-full", stop_if_running=stop_if_running)


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
    global _JOB_ACTIVE, _JOB_NAME
    with _JOB_LOCK:
        if _JOB_ACTIVE:
            if stop_if_running:
                _JOB_CANCELLED.set()
                voice.stop_speech()
            return False

        _JOB_ACTIVE = True
        _JOB_NAME = name
        _JOB_CANCELLED.clear()
        voice.reset_stop()
        threading.Thread(target=target, name=name, daemon=True).start()
        return True


def stop_talking() -> None:
    """The Control key: stop Murmur's speech now, like VoiceOver does.

    A fix keeps going (only its current line goes quiet), so pressing Control
    while Codex works never throws the fix away. Anything else (an announcement,
    Read full output, Demo mode) stops completely.
    """
    with _JOB_LOCK:
        running = _JOB_NAME if _JOB_ACTIVE else None
        if running is not None and running != "murmur-fix":
            _JOB_CANCELLED.set()
            voice.stop_speech()
            return
    voice.hush()


def start_announcement(*, stop_if_running: bool = False) -> bool:
    """Start one background announcement, optionally stopping the current one."""
    return _start_background_job(
        _run_announcement_job,
        "murmur-announcement",
        stop_if_running=stop_if_running,
    )


def start_auto_announcement(finished: FinishedCommand) -> bool:
    """Called by the background checker. Never interrupts Murmur if it is already talking."""

    def job() -> None:
        global _JOB_ACTIVE
        try:
            announce_finished_command(finished, _JOB_CANCELLED)
        finally:
            with _JOB_LOCK:
                _JOB_ACTIVE = False

    started = _start_background_job(job, "murmur-auto-announcement")
    if not started:
        print("Auto-announce skipped: Murmur is already speaking.")
    return started


_AUTO_LABELS = {
    "off": "Off: only speak when you ask",
    "errors": "Errors only: speak about errors; successes get the pass sound",
    "short": "Errors and short output: errors, plus short output read word for word",
    "everything": "Everything: errors, and all output read word for word",
}


def auto_choice() -> str:
    """The Auto-announce menu choice: "off", "errors", "short" or "everything"."""
    return auto_detail() if settings.get("auto_announce") else "off"


def auto_announce_status() -> str:
    if settings.get("auto_announce"):
        return (
            f"Murmur auto-announce: {config.AUTO_DETAILS[auto_detail()]}. "
            "Murmur speaks up when a Terminal command finishes."
        )
    return "Murmur auto-announce: off. Turn it on in the Murmur menu."


def start_auto_announcer() -> AutoAnnouncer:
    global _AUTO_ANNOUNCER
    _AUTO_ANNOUNCER = AutoAnnouncer(start_auto_announcement)
    _AUTO_ANNOUNCER.start()
    print(auto_announce_status())
    return _AUTO_ANNOUNCER


def _run_fix_job() -> None:
    global _JOB_ACTIVE
    try:
        fixer.fix_last_failure(_JOB_CANCELLED)
    finally:
        with _JOB_LOCK:
            _JOB_ACTIVE = False


def _start_fix_when_free() -> None:
    # Wait a moment for the speech we just stopped to wind down, then start the fix.
    for _ in range(50):
        with _JOB_LOCK:
            free = not _JOB_ACTIVE
        if free:
            break
        time.sleep(0.1)
    if not _start_background_job(_run_fix_job, "murmur-fix"):
        print("Fix it skipped: Murmur is still busy. Try again in a second.")


def start_fix() -> bool:
    """Ask Codex to fix the last failure in the background (the menu item and ⌘⌥⇧F).

    - If Murmur is talking (say, still reading the error), it stops talking and starts the fix.
    - If a fix is already running, this cancels it, like every other shortcut does.
    """
    with _JOB_LOCK:
        running = _JOB_NAME if _JOB_ACTIVE else None
    if running == "murmur-fix":
        print("Fix it: cancelling the fix. Anything Codex half-changed will be put back.")
        _start_background_job(_run_fix_job, "murmur-fix", stop_if_running=True)
        return False
    if running is not None:
        _start_background_job(_run_fix_job, "murmur-fix", stop_if_running=True)  # stops the talking
        threading.Thread(target=_start_fix_when_free, name="murmur-fix-waiter", daemon=True).start()
        return True
    return _start_background_job(_run_fix_job, "murmur-fix")


def _run_undo_job() -> None:
    global _JOB_ACTIVE
    try:
        fixer.undo_last_fix()
    finally:
        with _JOB_LOCK:
            _JOB_ACTIVE = False


def start_undo() -> bool:
    """Undo the last fix in the background, so speaking the result never freezes the menu."""
    return _start_background_job(_run_undo_job, "murmur-undo")


def _run_reset_demo_job() -> None:
    global _JOB_ACTIVE
    try:
        print("----- Murmur: Reset demo project -----")
        try:
            line = demo.reset_demo_project()
        except demo.DemoProblem as problem:
            line = str(problem)
        print(line)
        engine = voice.speak(line, mode=personality.get_mode())
        print(f"Voice engine: {engine}")
        print("----- End of Murmur reset -----")
    finally:
        with _JOB_LOCK:
            _JOB_ACTIVE = False


def _run_demo_mode_job() -> None:
    global _JOB_ACTIVE
    try:
        demo.run_demo_mode(_JOB_CANCELLED, announce=announce_last_output)
    finally:
        with _JOB_LOCK:
            _JOB_ACTIVE = False


def start_demo_mode() -> bool:
    """Replay the recorded demo in the background. Any Murmur shortcut stops it."""
    return _start_background_job(_run_demo_mode_job, "murmur-demo-mode")


def start_reset_demo() -> bool:
    """Put the demo project back to its buggy start, in the background."""
    return _start_background_job(_run_reset_demo_job, "murmur-reset-demo")


def open_last_fix_diff() -> bool:
    """Open the last fix's exact change in TextEdit, which VoiceOver reads well."""
    if not fixer.DIFF_PATH.exists():
        print("Murmur: there's no fix diff to open yet.")
        return False
    subprocess.run(["open", "-e", str(fixer.DIFF_PATH)], check=False)
    return True


KEY_PROMPT_MESSAGE = (
    "Paste your OpenAI API key. Murmur keeps it in your Mac's Keychain and only sends it to OpenAI. "
    "Without a key, Murmur still works and speaks with the Mac voice."
)


def ask_for_api_key(*, first_run: bool = False, make_window: Callable[..., Any] = rumps.Window) -> bool:
    """Show a secure text box for the API key and save it in the Keychain. True if saved."""
    _bring_to_front()
    window = make_window(
        message=KEY_PROMPT_MESSAGE,
        title="OpenAI API key",
        default_text="",
        ok="Save",
        cancel="Use the Mac voice" if first_run else "Cancel",
        dimensions=(360, 24),
        secure=True,  # dots instead of letters, like a password
    )
    response = window.run()
    if response.clicked != 1:
        print("Murmur: no API key saved. Speech uses the Mac voice.")
        return False
    key = response.text.strip()
    try:
        keychain.save_key(key)
    except ValueError as error:
        print(f"Murmur: API key not saved. {error}")
        rumps.alert(title="Key not saved", message=str(error))
        return False
    config.use_api_key(key, "the Keychain")
    voice.forget_client()
    voice.warm_up()
    print("Murmur: API key saved in the Keychain. OpenAI speech and summaries are on.")
    return True


def _bring_to_front() -> None:
    # Murmur has no Dock icon, so without this the box could open behind other windows.
    try:
        import AppKit

        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


def ask_for_api_key_if_first_run() -> bool:
    """On the very first start without a key, ask once. Afterwards it's in the menu."""
    if config.OPENAI_API_KEY or settings.get("key_prompt_done"):
        return False
    settings.set("key_prompt_done", True)
    return ask_for_api_key(first_run=True)


def api_key_status() -> str:
    if config.OPENAI_API_KEY:
        return f"Murmur: using the OpenAI API key from {config.API_KEY_SOURCE}."
    return "Murmur: no OpenAI API key yet, so speech uses the Mac voice."


def open_log() -> bool:
    """Open Murmur's log in TextEdit. Only the built app writes one; `uv run` prints to the terminal."""
    if not packaged.LOG_PATH.exists():
        print("Murmur: there's no log file. With `uv run`, everything prints in the terminal.")
        return False
    subprocess.run(["open", "-e", str(packaged.LOG_PATH)], check=False)
    return True


def check_codex_on_startup() -> None:
    """Say up front if Fix it can't work yet, instead of surprising you later."""
    problem = fixer.codex_ready()
    if problem:
        print(f"Murmur startup warning: Fix it ({config.FIX_HOTKEY_LABEL}) is unavailable. {problem}", file=sys.stderr)


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
            lambda: start_announcement(stop_if_running=True),
            on_fix=start_fix,
            on_read=lambda: start_read_full(stop_if_running=True),
            on_stop=stop_talking,
        )
        print(
            f"Murmur hotkey: {hotkey.shortcuts_label()} starts announcements; "
            "press either again to stop speech. "
            f"{config.FIX_HOTKEY_LABEL} asks Codex to fix the last error. "
            f"{config.READ_HOTKEY_LABEL} reads the full output word for word. "
            "Control stops speech."
        )
        return True
    except Exception as error:
        print(
            f"Murmur startup warning: hotkeys {config.HOTKEY_LABEL}, "
            f"{hotkey.double_tap_label()}, {config.FIX_HOTKEY_LABEL} and "
            f"{config.READ_HOTKEY_LABEL} are unavailable. {error}",
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


def _voice_choices() -> list[tuple[str, str, str]]:
    """The Voice menu: (choice, title, what VoiceOver says for it)."""
    choices = [
        ("voiceover", "VoiceOver", "VoiceOver: your own VoiceOver voice, rate and braille display"),
        ("mac", "Mac voice", "Mac voice: fast, works offline"),
    ]
    for name, title in config.VOICES.items():
        choices.append((name, f"Natural voice: {title}", f"Natural voice {title}, from OpenAI, needs the internet"))
    return choices


def _current_voice_choice() -> str:
    """Which Voice menu item gets the checkmark."""
    chosen = voice.chosen_engine()
    if chosen == "natural":
        return voice.current_voice()
    if chosen == "auto":
        return voice.engine()  # VoiceOver if it's running, otherwise the Mac voice
    return chosen


def current_voice_name() -> str:
    """Words for who is talking, like "Mac voice" or "Natural voice: Marin"."""
    chosen = voice.chosen_engine()
    if chosen == "natural":
        return f"Natural voice: {config.VOICES[voice.current_voice()]}"
    if chosen == "voiceover" and voice.engine() != "voiceover":
        return "VoiceOver (off, so Mac voice)"
    return config.ENGINES[voice.engine()]


_PERSONALITY_LABELS = {
    personality.Mode.BRIEF: "Brief: status first, then file, line and exact cause",
    personality.Mode.FRIENDLY: "Friendly: warm and a little playful, a few more words",
}


class MurmurApp(rumps.App):
    """The Murmur menu bar item and everything in its menu.

    Every item has plain spoken words for VoiceOver, and each submenu's title
    says the current choice, like "Voice: Marin", so you hear it without opening it.
    """

    def __init__(self) -> None:
        super().__init__("Murmur", quit_button="Quit Murmur")

        self.read_item = rumps.MenuItem(read_last_output_title(), callback=self.read_last_output)
        self.read_full_item = rumps.MenuItem(
            f"Read full output ({config.READ_HOTKEY_LABEL})", callback=self.read_full
        )
        _speak_label(
            self.read_full_item,
            f"Read full output, word for word. Shortcut: {config.READ_HOTKEY_SPOKEN}",
        )
        # Auto-announce: off, or how much to say when a command finishes on its own.
        self.auto_menu = rumps.MenuItem("Auto-announce")
        self.auto_items = {}
        for choice, name in (("off", "Off"), *config.AUTO_DETAILS.items()):
            item = rumps.MenuItem(name, callback=self._auto_chooser(choice))
            _speak_label(item, _AUTO_LABELS[choice])
            self.auto_items[choice] = item
            self.auto_menu.add(item)

        self.personality_menu = rumps.MenuItem("Style")
        self.personality_items = {}
        for mode in personality.Mode:
            item = rumps.MenuItem(mode.value, callback=self._personality_chooser(mode))
            _speak_label(item, _PERSONALITY_LABELS[mode])
            self.personality_items[mode] = item
            self.personality_menu.add(item)

        # Voice: VoiceOver, the Mac voice, or a Natural (OpenAI) voice. One list, one choice.
        self.voice_menu = rumps.MenuItem("Voice")
        self.voice_items = {}
        for choice, name, label in _voice_choices():
            item = rumps.MenuItem(name, callback=self._voice_chooser(choice))
            _speak_label(item, label)
            self.voice_items[choice] = item
            self.voice_menu.add(item)

        self.speed_menu = rumps.MenuItem("Speed")
        self.speed_items = {}
        for choice, name in config.SPEEDS.items():
            item = rumps.MenuItem(name, callback=self._speed_chooser(choice))
            self.speed_items[choice] = item
            self.speed_menu.add(item)

        self.tap_menu = rumps.MenuItem("Double-tap key")
        self.tap_items = {}
        for choice, name in config.DOUBLE_TAP_KEYS.items():
            item = rumps.MenuItem(name, callback=self._tap_chooser(choice))
            _speak_label(item, f"{name}: tap it twice to read the last output")
            self.tap_items[choice] = item
            self.tap_menu.add(item)

        # Fix it. These three dim themselves when there's nothing for them to do.
        self.fix_item = rumps.MenuItem(f"Fix last error ({config.FIX_HOTKEY_LABEL})")
        self.undo_item = rumps.MenuItem("Undo last fix")
        self.diff_item = rumps.MenuItem("Open last fix diff")

        self.captions_item = rumps.MenuItem("Show captions", callback=self.toggle_captions)
        self.demo_item = rumps.MenuItem("Demo mode (replay)", callback=self.demo_mode)
        _speak_label(
            self.demo_item,
            "Demo mode: replay a recorded failure and fix, with captions. No Terminal or internet needed",
        )
        self.reset_demo_item = rumps.MenuItem("Reset demo project", callback=self.reset_demo)
        _speak_label(self.reset_demo_item, "Reset demo project: put the demo's bug back, ready to fix again")

        self.log_item = rumps.MenuItem("Show log", callback=self.show_log)
        _speak_label(self.log_item, "Show log: everything Murmur printed, in TextEdit")
        self.key_item = rumps.MenuItem("Set OpenAI API key…", callback=self.set_api_key)
        _speak_label(self.key_item, "Set OpenAI API key: saved in your Mac's Keychain")

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
                self.read_full_item,
                self.auto_menu,
                None,
                self.fix_item,
                self.undo_item,
                self.diff_item,
                None,
                self.personality_menu,
                self.voice_menu,
                self.speed_menu,
                self.tap_menu,
                self.captions_item,
                None,
                self.demo_item,
                self.reset_demo_item,
                None,
                self.key_item,
                self.log_item,
                self.test_voice_item,
                self.welcome_item,
            ]
        )
        self._show_personality_choice()
        self._show_voice_choice()
        self._show_speed_choice()
        self._show_tap_choice()
        self._show_auto_choice()
        self._show_captions_choice()
        self.show_fix_choices()
        rumps.events.before_start.register(self._label_menu_bar_item)
        # Fixes finish in the background, so check once a second whether to dim or light these up.
        self._fix_timer = rumps.Timer(lambda _timer: self.show_fix_choices(), 1)
        rumps.events.before_start.register(self._fix_timer.start)

    @staticmethod
    def _offer(item: rumps.MenuItem, callback, available: bool, label: str, why_not: str) -> None:
        # No callback means macOS dims the item. VoiceOver says "dimmed" itself; we add why.
        item.set_callback(callback if available else None)
        _speak_label(item, label if available else f"{label}. Unavailable: {why_not}")

    def show_fix_choices(self) -> None:
        self._show_captions_choice()  # Demo mode turns captions on from the background
        self._offer(
            self.fix_item,
            self.fix_last_error,
            fixer.last_failure() is not None,
            f"Fix last error with Codex. Shortcut: {config.FIX_HOTKEY_SPOKEN}",
            "no failed command to fix",
        )
        self._offer(
            self.undo_item, self.undo_last_fix, fixer.last_fix() is not None, "Undo last fix", "no fix to undo"
        )
        self._offer(
            self.diff_item,
            self.open_last_fix_diff,
            fixer.DIFF_PATH.exists(),
            "Open last fix diff in TextEdit",
            "Codex hasn't made a fix yet",
        )

    def fix_last_error(self, _sender: object) -> None:
        start_fix()

    def undo_last_fix(self, _sender: object) -> None:
        start_undo()

    def open_last_fix_diff(self, _sender: object) -> None:
        open_last_fix_diff()

    def _auto_chooser(self, choice: str) -> Callable[[object], None]:
        def choose(_sender: object) -> None:
            self.choose_auto(choice)

        return choose

    def choose_auto(self, choice: str) -> None:
        """choice is "off", "errors", "short" or "everything"."""
        settings.set("auto_announce", choice != "off")
        if choice != "off":
            settings.set("auto_detail", choice)
        self._show_auto_choice()
        print(auto_announce_status())

    def _show_auto_choice(self) -> None:
        current = auto_choice()
        for choice, item in self.auto_items.items():
            item.state = int(choice == current)
        name = "Off" if current == "off" else config.AUTO_DETAILS[current]
        self._show_submenu_choice(self.auto_menu, "Auto-announce", name)

    def toggle_captions(self, _sender: object) -> None:
        captions.CAPTIONS.set_enabled(not captions.enabled())
        self._show_captions_choice()

    def _show_captions_choice(self) -> None:
        on = captions.enabled()
        self.captions_item.state = int(on)
        _speak_label(
            self.captions_item,
            f"Show captions: big on-screen text of what Murmur says, for the room. {'On' if on else 'Off'}",
        )

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
        self._show_submenu_choice(self.personality_menu, "Style", current.value)

    def choose_brief(self, _sender: object) -> None:
        self._choose_personality(personality.Mode.BRIEF)

    def choose_friendly(self, _sender: object) -> None:
        self._choose_personality(personality.Mode.FRIENDLY)

    def _voice_chooser(self, choice: str) -> Callable[[object], None]:
        def choose(_sender: object) -> None:
            self.choose_voice(choice)

        return choose

    def choose_voice(self, choice: str) -> None:
        """choice is "voiceover", "mac", or a Natural voice like "marin"."""
        if choice in config.VOICES:
            settings.set("engine", "natural")
            settings.set("voice", choice)
        else:
            settings.set("engine", choice)
        self._show_voice_choice()
        self._show_speed_choice()
        print(f"Murmur voice: now {current_voice_name()}.")

    def _show_voice_choice(self) -> None:
        current = _current_voice_choice()
        for choice, item in self.voice_items.items():
            item.state = int(choice == current)
        self._show_submenu_choice(self.voice_menu, "Voice", current_voice_name())

    def _speed_chooser(self, choice: str) -> Callable[[object], None]:
        def choose(_sender: object) -> None:
            self.choose_speed(choice)

        return choose

    def choose_speed(self, choice: str) -> None:
        settings.set("speed", choice)
        self._show_speed_choice()
        print(f"Murmur speed: now {config.SPEEDS[choice]}.")

    def _show_speed_choice(self) -> None:
        current = voice.speed_choice()
        for choice, item in self.speed_items.items():
            item.state = int(choice == current)
        self._show_submenu_choice(self.speed_menu, "Speed", config.SPEEDS[current])
        if voice.engine() == "voiceover":
            # The menu still works, but VoiceOver talks at your own VoiceOver rate.
            _speak_label(self.speed_menu, f"Speed, currently {config.SPEEDS[current]}. "
                         "VoiceOver uses its own rate, set in VoiceOver")

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

    def read_full(self, _sender: object) -> None:
        start_read_full()

    def read_last_output(self, _sender: object) -> None:
        start_announcement()

    def demo_mode(self, _sender: object) -> None:
        start_demo_mode()

    def reset_demo(self, _sender: object) -> None:
        start_reset_demo()

    def show_log(self, _sender: object) -> None:
        open_log()

    def set_api_key(self, _sender: object) -> None:
        ask_for_api_key()

    def test_voice(self, _sender: object) -> None:
        start_test_voice()

    def replay_welcome(self, _sender: object) -> None:
        start_welcome()


def main() -> None:
    packaged.prepare()  # inside Murmur.app: log file and tool PATH; does nothing with `uv run`
    for warning in config.startup_warnings():
        print(warning, file=sys.stderr)
    print(api_key_status())
    voice.warm_up()  # open the OpenAI connection now, so the first line starts sooner
    voice.on_speech(captions.CAPTIONS.heard)  # the caption window follows every word
    check_terminal_on_startup()
    check_codex_on_startup()
    start_hotkey()
    start_auto_announcer()
    welcome_if_first_run()
    murmur_app = MurmurApp()
    # The key box needs the app running, so ask a moment after it starts.
    rumps.events.before_start.register(lambda: _later(1.0, ask_for_api_key_if_first_run))
    murmur_app.run()


def _later(seconds: float, work: Callable[[], object]) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callLater(seconds, work)


if __name__ == "__main__":
    main()
