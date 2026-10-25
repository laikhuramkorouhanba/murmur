"""End-to-end self-test of Murmur's real pipeline, without the menu bar.

    uv run python -m murmur.selftest              # quiet (same as --muted)
    uv run python -m murmur.selftest --speak      # play the audio so you can listen
    uv run python -m murmur.selftest --no-codex   # apply a known patch instead of calling Codex
    uv run python -m murmur.selftest --demo-mode  # also rehearse Demo mode offline

It opens its own new Terminal window and only ever talks to that tab, found by
its tty. Your own windows are never typed into, read, or closed. Then it:
rebuilds the demo, runs the failing test, waits for auto-announce, runs Fix it
(the same function ⌘⌥⇧F calls), checks the fix, undoes it, and closes its window.

Exit code 0 only if every step passes.
"""

import argparse
import io
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from murmur import app, demo, fixer, monitor, personality, prerender, settings, voice, watcher
from murmur.watcher import TabSnapshot

REPO = Path(__file__).resolve().parents[2]
DEMO = Path.home() / "murmur-fixme-demo"
TEST_COMMAND = f'uv run --project "{REPO}" pytest -q shop_demo_check.py'
LOG_PATH = Path.home() / ".murmur" / "selftest.log"

# The fix --no-codex applies: exactly what Codex should do to the demo bug.
BUGGY_LINE = "total = subtotal - subtotal * tax_rate"
FIXED_LINE = "total = subtotal + subtotal * tax_rate"
KNOWN_SUMMARY = "Changed order_total in shop.py to add the sales tax instead of subtracting it."


# ---------------------------------------------------------------------------
# The test's own Terminal tab. Every AppleScript here finds the tab by its tty.
# ---------------------------------------------------------------------------

_BY_TTY = '''
on run argv
    set wanted to item 1 of argv
    set action to item 2 of argv
    tell application "Terminal"
        repeat with w from 1 to count of windows
            repeat with t from 1 to count of tabs of window w
                if tty of tab t of window w is wanted then
                    if action is "busy" then return (busy of tab t of window w) as text
                    if action is "send" then
                        do script (item 3 of argv) in tab t of window w
                        return "sent"
                    end if
                    if action is "close" then
                        if (count of tabs of window w) is not 1 then return "shared"
                        close window w
                        return "closed"
                    end if
                end if
            end repeat
        end repeat
    end tell
    return "missing"
end run
'''


class TestTab:
    """A brand-new Terminal window that belongs to the self-test, and nothing else."""

    def __init__(self) -> None:
        answer = subprocess.run(
            ["osascript", "-e", 'tell application "Terminal"\nset t to do script ""\ndelay 1\nreturn tty of t\nend tell'],
            capture_output=True, text=True, timeout=20, check=False,
        )
        tty = answer.stdout.strip()
        if not re.fullmatch(r"/dev/ttys\d+", tty):
            raise RuntimeError(f"Couldn't open a test window: {answer.stderr.strip() or tty!r}")
        self.tty = tty

    def _ask(self, action: str, *extra: str) -> str:
        answer = subprocess.run(
            ["osascript", "-", self.tty, action, *extra],
            input=_BY_TTY, capture_output=True, text=True, timeout=20, check=False,
        )
        if answer.returncode != 0:
            raise RuntimeError(answer.stderr.strip() or "AppleScript failed")
        return answer.stdout.strip()

    def busy(self) -> bool:
        return self._ask("busy") == "true"

    def snapshot(self) -> TabSnapshot:
        """What auto-announce sees, but for this tab instead of whichever window is in front."""
        return TabSnapshot(tty=self.tty, busy=self.busy(), processes=("login", "-zsh"))

    def history(self) -> str:
        return watcher.get_tab_text(self.tty)

    def send(self, command: str) -> None:
        if self._ask("send", command) != "sent":
            raise RuntimeError("The test window is gone.")

    def run(self, command: str, timeout: float = 90) -> str:
        """Type a command into this tab, wait for the next prompt, and return its output."""
        before = len(self.history())
        self.send(command)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.5)
            history = self.history()
            if len(history) > before and _ends_with_empty_prompt(history) and not self.busy():
                return watcher.last_command_output(history)
        raise RuntimeError(f"Timed out after {timeout:.0f} s waiting for: {command}")

    def close(self) -> str:
        return self._ask("close")


def _ends_with_empty_prompt(text: str) -> bool:
    lines = [line.rstrip() for line in text.rstrip().split("\n")]
    match = watcher._ZSH_PROMPT.match(lines[-1]) if lines else None
    return bool(match and not match.group("rest").strip())


# ---------------------------------------------------------------------------
# Steps and the results table
# ---------------------------------------------------------------------------


@dataclass
class Step:
    name: str
    seconds: float = 0.0
    problems: list[str] = field(default_factory=list)  # empty means PASS
    note: str = ""  # extra information for the table, never a failure
    mark: int = 0  # where this step's speech starts in the recorder
    spoken: list[voice.SpeechReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def fail(self, problem: str) -> None:
        self.problems.append(problem)


class Recorder:
    """Collects every spoken line, so each step knows what Murmur said during it."""

    def __init__(self) -> None:
        self.reports: list[voice.SpeechReport] = []
        self.started: list[tuple[float, str]] = []  # when each line began, from voice.on_speech
        self._lock = threading.Lock()

    def heard_start(self, text: str | None) -> None:
        if text is not None:
            with self._lock:
                self.started.append((time.monotonic(), text))

    def first_start_after(self, moment: float, containing: str) -> float | None:
        with self._lock:
            return next((when for when, text in self.started if when >= moment and containing in text), None)

    def __call__(self, report: voice.SpeechReport) -> None:
        with self._lock:
            self.reports.append(report)

    def mark(self) -> int:
        with self._lock:
            return len(self.reports)

    def since(self, mark: int) -> list[voice.SpeechReport]:
        with self._lock:
            return self.reports[mark:]


def wait_until(condition: Callable[[], bool], timeout: float, every: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(every)
    return condition()


def wait_for_job(timeout: float) -> bool:
    """Wait for Murmur's current background job (announcement, fix, undo) to finish."""
    return wait_until(lambda: not app._JOB_ACTIVE, timeout)


def known_patch(failure, root, cancel, on_tick=None, log_file=None) -> fixer.CodexAnswer:
    """--no-codex: behave like a quick Codex run, then make the one-line fix ourselves."""
    started = time.monotonic()
    while time.monotonic() - started < 1.5:
        if on_tick:
            on_tick(time.monotonic() - started)  # the first tick says the "working on it" line
        if cancel.wait(0.5):
            raise fixer.FixRefused("Fix cancelled.")
    shop = root / "shop.py"
    shop.write_text(shop.read_text(encoding="utf-8").replace(BUGGY_LINE, FIXED_LINE), encoding="utf-8")
    return fixer.CodexAnswer(fixed=True, summary=KNOWN_SUMMARY, verified=True)


def _working_cues() -> set[str]:
    friendly, brief = personality._FIX_LINES["working"]
    return {*friendly, brief}


def _short(seconds: float | None) -> str:
    return "-" if seconds is None else f"{seconds:.1f} s"


def print_table(steps: list[Step], out) -> None:
    headers = ("Step", "Result", "Time", "Engine", "1st audio", "Spoken text")
    widths = (24, 6, 7, 20, 9)
    text_width = 64

    def row(cells, spoken=""):
        left = "  ".join(str(cell).ljust(width) for cell, width in zip(cells, widths))
        wrapped = textwrap.wrap(spoken, text_width) or [""]
        print(f"{left}  {wrapped[0]}", file=out)
        for more in wrapped[1:]:
            print(f"{' ' * len(left)}  {more}", file=out)

    print("", file=out)
    row(headers[:5], headers[5])
    print("-" * (sum(widths) + 2 * len(widths) + text_width), file=out)
    for step in steps:
        result = "PASS" if step.ok else "FAIL"
        lines = step.spoken or [None]
        for index, report in enumerate(lines):
            cells = (step.name, result, _short(step.seconds)) if index == 0 else ("", "", "")
            if report is None:
                row((*cells, "-", "-"), "")
            else:
                row((*cells, report.engine, _short(report.first_audio_seconds)), report.text)
        for problem in step.problems:
            row(("", "", "", "", ""), f"✗ {problem}")
        if step.note:
            row(("", "", "", "", ""), f"↳ {step.note}")
    passed = sum(step.ok for step in steps)
    print(f"\n{passed} of {len(steps)} steps passed.", file=out)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


class Tee(io.TextIOBase):
    """Everything Murmur prints goes to the log file (and stays out of the table)."""

    def __init__(self, log) -> None:
        self.log = log
        self.text: list[str] = []

    def write(self, text: str) -> int:
        self.log.write(text)
        self.text.append(text)
        return len(text)

    def flush(self) -> None:
        self.log.flush()

    def joined(self) -> str:
        return "".join(self.text)


def run(no_codex: bool, muted: bool, demo_mode: bool = False) -> int:
    out = sys.__stdout__
    say = lambda message: print(message, file=out, flush=True)  # noqa: E731

    if subprocess.run(["pgrep", "-f", "murmur.app|Murmur.app/Contents/MacOS"], capture_output=True).stdout.strip():
        say("Note: another Murmur is running. It may also speak about the test window.")

    voice.set_muted(muted)
    recorder = Recorder()
    voice.on_spoken(recorder)
    voice.on_speech(recorder.heard_start)
    scratch = Path(tempfile.mkdtemp(prefix="murmur-selftest-"))
    fixer.DIFF_PATH = scratch / "last_fix.diff"  # keep your real "last fix" files untouched
    fixer.LOG_PATH = scratch / "last_fix_codex.log"
    if no_codex:
        fixer.run_codex = known_patch
        fixer.codex_ready = lambda: None

    # Keep your settings file exactly as it was, even keys you never set. The test
    # checks the default Brief wording and short-output reading, so it uses those.
    saved_settings = settings.PATH.read_bytes() if settings.PATH.exists() else None
    settings.set("style", personality.Mode.BRIEF.value)
    settings.set("auto_detail", "short")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG_PATH, "w", encoding="utf-8")
    tee = Tee(log)
    sys.stdout = sys.stderr = tee

    steps: list[Step] = []
    tab: TestTab | None = None
    mode = "muted" if muted else "speaking"
    say(f"Murmur self-test ({mode}, {'known patch' if no_codex else 'real Codex'}). Full log: {LOG_PATH}")

    def step(name: str, action: Callable[[Step], None]) -> bool:
        current = Step(name, mark=recorder.mark())
        steps.append(current)
        say(f"  … {name}")
        started = time.monotonic()
        try:
            action(current)
        except Exception as error:  # a step failing must never leave the window open
            current.fail(f"{type(error).__name__}: {error}")
        current.seconds = time.monotonic() - started
        current.spoken = recorder.since(current.mark)
        return current.ok

    try:
        # 1. Our own window, with the virtual env's "(murmur)" in the prompt.
        def open_tab(current: Step) -> None:
            nonlocal tab
            tab = TestTab()
            tab.run(f'cd "{REPO}" && source .venv/bin/activate')
            prompt = tab.history().rstrip().split("\n")[-1]
            if not prompt.startswith("("):
                current.fail(f"no virtual env name in the prompt: {prompt!r}")
            current.note = f"tab {tab.tty}, prompt starts {prompt.split(' ')[0]}"

        if not step("Open test window (venv)", open_tab):
            return finish(steps, tab, out)

        # 2. A fresh copy of the demo.
        def rebuild(current: Step) -> None:
            output = tab.run(f'"{REPO}/demo/make_fixme_project.sh"')
            if "Created the Fix it demo" not in output:
                current.fail("the demo builder didn't report success")

        step("Rebuild demo project", rebuild)

        # 3. The failing test, caught by the real auto-announce chain, in Brief wording.
        def auto_announce(current: Step) -> None:
            with Announcing(tab):
                sent = time.monotonic()
                tab.send(f'cd "{DEMO}" && {TEST_COMMAND}')
                heard = wait_until(lambda: any("Failed." in r.text for r in recorder.since(current.mark)), timeout=45)
                began = recorder.first_start_after(sent, "Failed.")
                if heard and began is not None:
                    current.note = f"started speaking {began - sent:.1f} s after the test was started"
                wait_for_job(30)
            if not heard:
                current.fail("auto-announce never spoke about the failure")
                return
            spoken = [r.text for r in recorder.since(current.mark)]
            exact = voice.engine() == "voiceover"
            place = "shop_demo_check.py, line 9" if exact else "shop demo check dot py, line 9"
            wanted = f"Failed. {place}. Expected 21.60, got 18.40."
            line = next((text for text in spoken if "Failed." in text), "")
            if line != wanted:
                current.fail(f"not Brief: {line!r}, wanted {wanted!r}")
            if not spoken or spoken[0] != voice.FAIL_SOUND_CAPTION:
                current.fail("the fail sound didn't play first")
            failure = fixer.last_failure()
            if failure is None or Path(failure.folder or "") != DEMO:
                current.fail(f"Fix it wouldn't know the project (remembered: {failure})")

        if not step("Auto-announce failure", auto_announce):
            return finish(steps, tab, out)

        # 4. Fix it, through the same function the ⌘⌥⇧F hotkey calls.
        def fix(current: Step) -> None:
            before = len(tee.joined())
            if not app.start_fix():
                current.fail("Fix it didn't start (Murmur was busy)")
                return
            time.sleep(0.3)
            wait_for_job(240)
            printed = tee.joined()[before:]
            spoken = [r.text for r in recorder.since(current.mark)]
            if not any(line in _working_cues() for line in spoken):
                current.fail("no working-on-it cue was spoken")
            summary = next((line for line in reversed(spoken) if "Undo last fix" in line), "")
            if "shop.py" not in summary:
                current.fail("no spoken summary naming shop.py")
            took = re.search(r"Fix took (\d+(?:\.\d+)?) s", printed)
            if not took:
                current.fail("the fix time wasn't printed")
            else:
                current.note = f"Murmur printed: Fix took {took.group(1)} s"
            last = fixer.last_fix()
            if last is None or last.files != ["shop.py"]:
                current.fail(f"the fix changed {last.files if last else 'nothing'}, not just shop.py")
            diff = fixer.DIFF_PATH.read_text(encoding="utf-8") if fixer.DIFF_PATH.exists() else ""
            touched = re.findall(r"^diff --git a/(\S+)", diff, re.MULTILINE)
            if touched != ["shop.py"]:
                current.fail(f"the diff touches {touched}")

        step("Fix it (⌘⌥⇧F path)", fix)

        # 5. The test passes now.
        def rerun_passes(current: Step) -> None:
            output = tab.run(TEST_COMMAND)
            if "1 passed" not in output:
                current.fail("the demo test still doesn't pass")

        step("Rerun test: passes", rerun_passes)

        # 6. Undo, then the test fails again.
        def undo(current: Step) -> None:
            if not app.start_undo():
                current.fail("Undo didn't start (Murmur was busy)")
                return
            time.sleep(0.3)
            wait_for_job(30)
            spoken = " ".join(r.text for r in recorder.since(current.mark))
            if "Undone" not in spoken:
                current.fail("Undo didn't say it was done")
            if BUGGY_LINE not in (DEMO / "shop.py").read_text(encoding="utf-8"):
                current.fail("shop.py wasn't put back")

        step("Undo last fix", undo)

        def rerun_fails(current: Step) -> None:
            output = tab.run(TEST_COMMAND)
            if "1 failed" not in output:
                current.fail("the test should fail again after Undo")

        step("Rerun test: fails again", rerun_fails)

        # 8. Short successful output is read word for word, after the pass sound.
        def short_output(current: Step) -> None:
            with Announcing(tab):
                tab.send("ls")
                heard = wait_until(lambda: any("shop.py" in r.text for r in recorder.since(current.mark)), timeout=20)
                wait_for_job(20)
            spoken = [r.text for r in recorder.since(current.mark)]
            if not heard:
                current.fail("auto-announce didn't read the ls output")
                return
            if spoken[0] != voice.PASS_SOUND_CAPTION:
                current.fail("the pass sound didn't play first")
            words = " ".join(spoken[1:])
            for name in ("README.md", "shop.py", "shop_demo_check.py"):
                if name not in words:
                    current.fail(f"{name} wasn't read out")
            current.note = "ls output read word for word"

        step("Auto-announce short output (ls)", short_output)

        step("Key layer swallows its key", key_layer_check)

        if demo_mode:
            demo_steps(step, recorder)
    finally:
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
        log.close()
        if saved_settings is None:
            settings.PATH.unlink(missing_ok=True)
        else:
            settings.PATH.write_bytes(saved_settings)
    return finish(steps, tab, out)


def key_layer_check(current: Step) -> None:
    """The real key-swallowing tap, tested with F19 and F18: keys that do nothing in your apps.

    A second, listen-only tap behind it plays Terminal: it must never see F19 while
    the layer is open, and must see F18 (not a layer key) as normal.
    """
    import Quartz

    from murmur import hotkey

    f18, f19 = 79, 80
    commands, behind = [], []

    def listen(_proxy, event_type, event, _refcon):
        if event_type == Quartz.kCGEventKeyDown:
            behind.append(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
        return event

    real_keys = hotkey.config.KEY_LAYER_KEYS
    hotkey.config.KEY_LAYER_KEYS = {f19: "test"}
    loops = []
    try:
        swallower = None
        layer = hotkey.KeyLayer(commands.append, on_change=lambda on: swallower and swallower.set_enabled(on))
        swallower = hotkey.KeySwallower(layer)
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        tail = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGTailAppendEventTap, Quartz.kCGEventTapOptionListenOnly, mask, listen, None
        )

        def run_tail() -> None:
            source = Quartz.CFMachPortCreateRunLoopSource(None, tail, 0)
            loops.append(Quartz.CFRunLoopGetCurrent())
            Quartz.CFRunLoopAddSource(loops[-1], source, Quartz.kCFRunLoopDefaultMode)
            Quartz.CFRunLoopRun()

        threading.Thread(target=run_tail, daemon=True).start()
        time.sleep(0.3)

        def press(code: int) -> None:
            for down in (True, False):
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, code, down))
            time.sleep(0.2)

        press(f19)  # layer closed: F19 must pass through untouched
        passed_when_closed = f19 in behind
        behind.clear()
        layer.open()
        started = time.monotonic()
        press(f19)  # layer open: swallowed, and the command runs
        swallow_seconds = time.monotonic() - started
        press(f18)  # not a layer key: passes through
        if not passed_when_closed:
            current.fail("with the layer closed, a key was still held back")
        if f19 in behind:
            current.fail("the layer's key reached the app behind it")
        if commands != ["test"]:
            current.fail(f"the layer command didn't run once (ran: {commands})")
        if f18 not in behind:
            current.fail("a key that isn't a layer key was held back")
        if layer.needs_swallowing():
            current.fail("the swallowing tap stayed on after the key")
        current.note = f"F19 swallowed, F18 passed, tap off again ({swallow_seconds:.2f} s)"
    finally:
        hotkey.config.KEY_LAYER_KEYS = real_keys
        for loop in loops:
            Quartz.CFRunLoopStop(loop)


class Announcing:
    """Run the real auto-announce chain on the test tab while inside this block."""

    def __init__(self, tab: TestTab) -> None:
        self._announcer = monitor.AutoAnnouncer(app.start_auto_announcement, snapshot=tab.snapshot, enabled=lambda: True)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)

    def _watch(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self._announcer.check_once())

    def __enter__(self):
        self._thread.start()
        time.sleep(1.5)  # let it take its first look at the tab before anything runs
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class Offline:
    """While active, any OpenAI speech request fails as if the Wi-Fi were off."""

    def __enter__(self):
        self._real = voice._openai_client

        def no_network():
            raise ConnectionError("offline rehearsal: no network")

        voice._openai_client = no_network
        return self

    def __exit__(self, *_exc) -> None:
        voice._openai_client = self._real


def demo_steps(step: Callable, recorder: "Recorder") -> None:
    """--demo-mode: rehearse Demo mode offline, then Undo, Reset, and the fallback order."""
    # Keep your settings file exactly as it was, even keys you never set.
    saved_file = settings.PATH.read_bytes() if settings.PATH.exists() else None
    # Pre-rendering covers the Natural voice Marin in Brief, so rehearse with those; your choices come back after.
    settings.set("style", personality.Mode.BRIEF.value)
    settings.set("engine", "natural")
    settings.set("voice", prerender.VOICE)
    recording = demo.load_recording()
    try:
        def demo_mode(current: Step) -> None:
            settings.set("captions", False)
            with Offline():
                if not app.start_demo_mode():
                    current.fail("Demo mode didn't start (Murmur was busy)")
                    return
                time.sleep(0.3)
                wait_for_job(90)
            spoken = recorder.since(current.mark)
            texts = [report.text for report in spoken]
            live = [report for report in spoken if report.engine not in ("pre-rendered", "sounddevice", "sounddevice (muted)")]
            if live:
                current.fail(f"{len(live)} line(s) weren't pre-rendered: {live[0].engine}: {live[0].text[:50]}")
            texts = [text for text in texts if not text.startswith("♪")]  # the pass and fail sounds
            if not texts or texts[0] != demo.INTRO_LINE:
                current.fail("it didn't start by saying it is a replay")
            if not any("Expected 21.60, got 18.40" in text for text in texts):
                current.fail("the recorded failure wasn't announced")
            if not any(recording["codex"]["summary"] in text for text in texts):
                current.fail("the recorded fix summary wasn't spoken")
            if not settings.get("captions"):
                current.fail("captions weren't switched on")
            if demo.BUGGY_LINE in (demo.DEMO_DIR / "shop.py").read_text(encoding="utf-8"):
                current.fail("the recorded fix wasn't applied")
            last = fixer.last_fix()
            if last is None or last.files != ["shop.py"]:
                current.fail("Undo wouldn't know about the demo fix")
            current.note = "offline: every OpenAI request was blocked"

        step("Demo mode (offline)", demo_mode)

        def undo_demo(current: Step) -> None:
            with Offline():
                app.start_undo()
                time.sleep(0.3)
                wait_for_job(30)
            if not demo.demo_project_ready():
                current.fail("the demo project didn't go back to its bug")
            if any(r.engine != "pre-rendered" for r in recorder.since(current.mark) if not r.text.startswith("♪")):
                current.fail("Undo's line wasn't pre-rendered")

        step("Undo demo fix (offline)", undo_demo)

        def reset(current: Step) -> None:
            (demo.DEMO_DIR / "shop.py").write_text("changed by the self-test\n", encoding="utf-8")
            with Offline():
                app.start_reset_demo()
                time.sleep(0.3)
                wait_for_job(60)
            texts = [r.text for r in recorder.since(current.mark)]
            if demo.RESET_LINE not in texts:
                current.fail("Reset didn't say the bug is back")
            if not demo.demo_project_ready():
                current.fail("the demo project isn't back to its starting state")
            if fixer.last_failure() is not None or fixer.last_fix() is not None:
                current.fail("Murmur still remembers the old failure or fix")

        step("Reset demo project", reset)

        def fallback(current: Step) -> None:
            # Hide one recording: with no file and no network, the Mac voice must take over.
            line = demo.RESET_LINE
            path = prerender.path_for(line, voice.speed_choice())
            hidden = path.with_suffix(".hidden")
            path.rename(hidden)
            try:
                with Offline():
                    engine = voice.speak(line, mode=personality.Mode.BRIEF)
            finally:
                hidden.rename(path)
            if engine != "macOS say":
                current.fail(f"expected the Mac voice, got {engine}")
            current.note = "file hidden + network off: pre-rendered → OpenAI → macOS say"

        step("Fallback order", fallback)
    finally:
        if saved_file is None:
            settings.PATH.unlink(missing_ok=True)
        else:
            settings.PATH.write_bytes(saved_file)


def finish(steps: list[Step], tab: "TestTab | None", out) -> int:
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    if tab is not None:
        closing = Step("Close test window")
        started = time.monotonic()
        try:
            answer = tab.close()
            if answer != "closed":
                closing.fail(f"window not closed ({answer})")
        except Exception as error:
            closing.fail(f"{type(error).__name__}: {error}")
        closing.seconds = time.monotonic() - started
        steps.append(closing)
    print_table(steps, out)
    return 0 if steps and all(step.ok for step in steps) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m murmur.selftest", description=__doc__.split("\n")[0])
    sound = parser.add_mutually_exclusive_group()
    sound.add_argument("--muted", action="store_true", help="no audio played; speech is still requested and timed (default)")
    sound.add_argument("--speak", action="store_true", help="play the audio so you can listen")
    parser.add_argument("--no-codex", action="store_true", help="apply a known patch instead of calling Codex")
    parser.add_argument(
        "--demo-mode", action="store_true", help="also rehearse Demo mode offline, Undo, Reset, and the fallback order"
    )
    options = parser.parse_args(argv)
    return run(no_codex=options.no_codex, muted=not options.speak, demo_mode=options.demo_mode)


if __name__ == "__main__":
    raise SystemExit(main())
