"""The stage demo: the demo project, Reset demo project, and Demo mode.

The demo project is a tiny shop checkout at ~/murmur-fixme-demo with one planted
bug. demo/make_fixme_project.sh builds it, and refuses to delete any folder that
it didn't make itself (it looks for a .murmur-demo marker).
"""

import json
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from murmur import captions, fixer, personality, voice

REPO = Path(__file__).resolve().parents[2]
DEMO_DIR = Path.home() / "murmur-fixme-demo"
BUILDER = REPO / "demo" / "make_fixme_project.sh"
RESET_LINE = "Demo project reset. The bug is back."
RECORDED = REPO / "demo" / "recorded"
INTRO_LINE = "Demo mode. This is a replay of a recorded run."
BUGGY_LINE = "total = subtotal - subtotal * tax_rate"
DEMO_PROMPT = "(murmur) you@mac murmur-fixme-demo %"
PAUSE_SECONDS = 1.5  # between the announcement and the fix, like a person reaching for ⌘⌥⇧F
FIX_SECONDS = 3.0  # how long the replayed "Codex" works (the real run took longer)


class DemoProblem(Exception):
    """The demo can't go ahead. The message is written to be spoken."""


def reset_demo_project() -> str:
    """Rebuild the demo project with its bug, and forget any old failure or fix. Returns the line to say."""
    if not BUILDER.exists():
        raise DemoProblem("I can't find the demo builder. Reset demo project needs Murmur's source folder.")
    result = subprocess.run([str(BUILDER)], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0:
        # The builder explains itself, for example when the folder isn't ours to delete.
        reason = (result.stderr.strip() or result.stdout.strip() or "it stopped").splitlines()[0]
        raise DemoProblem(f"The demo project wasn't reset: {reason}")
    fixer.forget_failure()
    fixer.remember_fix(None)  # that fix belonged to the old copy
    fixer.DIFF_PATH.unlink(missing_ok=True)
    return RESET_LINE


# ---------------------------------------------------------------------------
# Demo mode: replay a real recorded run, with no Terminal, network or Codex.
# ---------------------------------------------------------------------------


def load_recording() -> dict:
    try:
        return json.loads((RECORDED / "demo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DemoProblem("I can't find the recorded demo. Run demo/record_demo.py once to make it.") from None


def recorded_history(recording: dict) -> str:
    """The recorded failure, laid out the way Terminal shows it, with prompts around it."""
    return f"{DEMO_PROMPT} {recording['command']}\n{recording['output']}\n{DEMO_PROMPT} "


def demo_project_ready() -> bool:
    """True when the demo project is ours, still has its bug, and has no leftover changes."""
    shop = DEMO_DIR / "shop.py"
    if not (DEMO_DIR / ".murmur-demo").exists() or not shop.exists():
        return False
    if BUGGY_LINE not in shop.read_text(encoding="utf-8"):
        return False
    status = subprocess.run(
        ["git", "-C", str(DEMO_DIR), "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    return status.returncode == 0 and not status.stdout.strip()


def replayed_codex(recording: dict) -> Callable[..., fixer.CodexAnswer]:
    """Stands in for run_codex: waits a moment, then applies the recorded change."""

    def run(failure, root, cancel, on_tick=None, log_file=None) -> fixer.CodexAnswer:
        started = time.monotonic()
        while time.monotonic() - started < FIX_SECONDS:
            if on_tick:
                on_tick(time.monotonic() - started)  # says the "working on it" line, like the real one
            if cancel.wait(0.25):
                raise fixer.FixRefused("Fix cancelled.")
        patch = subprocess.run(
            ["git", "-C", str(root), "apply", "--whitespace=nowarn", str(RECORDED / "fix.diff")],
            capture_output=True, text=True, check=False,
        )
        if patch.returncode != 0:
            raise fixer.FixRefused("The recorded fix didn't fit the demo project. Try Reset demo project.")
        return fixer.CodexAnswer(**recording["codex"])

    return run


def run_demo_mode(cancel: threading.Event, *, announce: Callable[..., None]) -> None:
    """The whole replay: intro, the failure announced, a pause, then Fix it.

    announce is app.announce_last_output, passed in so this file doesn't import the app.
    It all goes through Murmur's real announcement and Fix it code; only the
    Terminal text and Codex's answer come from the recording. Undo and Open last
    fix diff work afterwards, just like after a real fix.
    """
    print("----- Murmur: Demo mode (replay of a recorded run) -----")
    try:
        recording = load_recording()
        captions.CAPTIONS.set_enabled(True)  # so the room can read along
        if not demo_project_ready():
            print(reset_demo_project())
        voice.speak(INTRO_LINE, mode=personality.get_mode())
        if cancel.is_set():
            return
        announce(cancel, read_text=lambda: recorded_history(recording), tty="demo-mode", folder=str(DEMO_DIR))
        if cancel.wait(PAUSE_SECONDS):
            return
        fixer.fix_last_failure(cancel, runner=replayed_codex(recording), ready=lambda: None)
    except DemoProblem as problem:
        print(problem)
        voice.speak(str(problem), mode=personality.get_mode())
    finally:
        print("----- End of Murmur Demo mode -----")
