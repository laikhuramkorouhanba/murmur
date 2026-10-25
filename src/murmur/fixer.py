"""Fix it: ask Codex to make the smallest fix for the last failing command.

This is where Murmur uses OpenAI Codex. The whole flow:
1. Murmur remembers the last failure it announced (the error text and the project folder).
2. On "Fix it", it takes a snapshot of the project so every change can be undone.
3. It runs `codex exec` in that project, sandboxed, with the error as context.
4. It compares the project before and after, prints the exact diff, and speaks a short summary.
5. "Undo last fix" puts the files back from the snapshot.
"""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from murmur import config, personality


@dataclass(frozen=True)
class LastFailure:
    """Everything Fix it needs about the command that just failed."""

    tty: str | None  # which Terminal tab it ran in
    folder: str | None  # where the shell was when it failed
    command: str | None  # the command line, like "pytest -q"
    output: str  # the cleaned output, error and all


_LOCK = threading.Lock()
_LAST_FAILURE: LastFailure | None = None


def remember_failure(failure: LastFailure | None) -> None:
    global _LAST_FAILURE
    with _LOCK:
        _LAST_FAILURE = failure


def forget_failure() -> None:
    """The last command worked, so there is nothing to fix any more."""
    remember_failure(None)


def last_failure() -> LastFailure | None:
    with _LOCK:
        return _LAST_FAILURE


# ---------------------------------------------------------------------------
# Safety: which folder Codex may touch, and snapshots so every fix can be undone.
# ---------------------------------------------------------------------------


class FixRefused(Exception):
    """Fix it can't go ahead. The message is written to be spoken aloud."""


def _git(root: Path | str, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise FixRefused(f"Git could not {args[0]}: {result.stderr.strip() or 'unknown problem'}")
    return result.stdout


def project_root(folder: str | None) -> Path:
    """The git project that the failing command ran in. Codex may only touch files in here."""
    if not folder:
        raise FixRefused("I don't know which folder that command ran in.")
    try:
        root = Path(_git(folder, "rev-parse", "--show-toplevel").strip()).resolve()
    except FixRefused:
        raise FixRefused("Fix it only works inside a git project, so every change can be undone.")
    if root in (Path.home().resolve(), Path("/")):
        raise FixRefused("That git project is your whole home folder, which is too big to hand to Codex.")
    return root


def snapshot(root: Path) -> str:
    """Save the project's current files inside git and return a short id for them.

    This uses a temporary, private git index. Your own staged changes, commits
    and branches are not touched. New files git doesn't track yet are included.
    """
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        try:
            _git(root, "read-tree", "HEAD", env=env)  # a quick start: begin from the last commit
        except FixRefused:
            pass  # a brand new repo with no commits yet; start from empty
        _git(root, "add", "-A", env=env)
        return _git(root, "write-tree", env=env).strip()


def changed_files(root: Path, before: str, after: str) -> list[tuple[str, str]]:
    """What changed between two snapshots, as (letter, path): A added, M modified, D deleted."""
    lines = _git(root, "diff", "--name-status", "--no-renames", before, after).splitlines()
    return [(line[0], line.split("\t", 1)[1]) for line in lines if "\t" in line]


def diff_text(root: Path, before: str, after: str, paths: list[str] | None = None) -> str:
    """The exact change, line by line, the same as `git diff` shows. Optionally just some files."""
    return _git(root, "diff", "--no-color", before, after, "--", *(paths or []))


# Files that tools make on their own when tests run. They aren't part of the fix,
# so we don't read them out or print their (binary) diffs. Undo still cleans them up.
_JUNK_FOLDERS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "node_modules"}
_JUNK_SUFFIXES = (".pyc", ".pyo")
_JUNK_NAMES = {".DS_Store", ".coverage"}


def is_junk(path: str) -> bool:
    """True for cache files like __pycache__/shop.cpython-313.pyc."""
    parts = Path(path).parts
    return (
        any(part in _JUNK_FOLDERS for part in parts[:-1])
        or path.endswith(_JUNK_SUFFIXES)
        or (bool(parts) and parts[-1] in _JUNK_NAMES)
    )


def undo(root: Path, before: str, after: str) -> list[str]:
    """Put the project back the way it was before the fix. Returns the paths it restored.

    If you edited one of those files after the fix, we stop and name it, so
    Undo never throws away your own work.
    """
    changes = changed_files(root, before, after)
    if not changes:
        return []
    paths = [path for _, path in changes]
    edited_since = _git(root, "diff", "--name-only", after, snapshot(root), "--", *paths).split()
    if edited_since:
        raise FixRefused(
            f"You changed {', '.join(edited_since)} after the fix, so I left everything as it is."
        )
    for letter, path in changes:
        if letter == "A":
            # Codex made this file; undo means it goes away again.
            (root / path).unlink(missing_ok=True)
        else:
            _git(root, "restore", f"--source={before}", "--worktree", "--", path)
    return paths


# ---------------------------------------------------------------------------
# Running Codex. This is the heart of the Codex integration.
# ---------------------------------------------------------------------------

# Codex must answer in this exact shape, so Murmur can speak it reliably.
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "fixed": {"type": "boolean", "description": "true if you changed files to fix the error"},
        "summary": {"type": "string", "description": "one short plain sentence: what you changed"},
        "verified": {"type": "boolean", "description": "true if you re-ran the command and it passed"},
    },
    "required": ["fixed", "summary", "verified"],
    "additionalProperties": False,
}

PROMPT = """\
A command failed in this project. A blind developer asked for a quick fix and will hear
your answer read aloud.

Make the SMALLEST change that fixes this exact error.
- Only edit files inside this project folder.
- Fix the code, not the test, unless the test itself is clearly wrong.
- Do not commit, do not install packages, do not use the network.
- If it is quick and safe, re-run the failing command once to check your fix.
- If you cannot find a safe fix, change nothing and say why.

Your summary must be one short plain sentence a screen reader can say, for example:
"Closed the missing bracket on line 12 of checkout.py."

The failing command was: {command}
Its output (the error) is attached below.
"""


@dataclass(frozen=True)
class CodexAnswer:
    fixed: bool
    summary: str
    verified: bool


def codex_ready() -> str | None:
    """None if Codex is installed and logged in, otherwise a plain explanation."""
    if shutil.which("codex") is None:
        return "Codex isn't installed. Install it with: brew install codex"
    try:
        status = subprocess.run(
            ["codex", "login", "status"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "Codex didn't answer when I checked whether it's logged in."
    if status.returncode != 0:
        return "Codex isn't logged in. Run: codex login"
    return None


def codex_command(root: Path, schema_file: Path, answer_file: Path, prompt: str) -> list[str]:
    """The exact `codex exec` command Murmur runs. Every safety choice is spelled out here."""
    return [
        "codex", "exec",
        "--cd", str(root),                      # work in this project only
        "--sandbox", "workspace-write",         # may edit files in the project, nothing outside it
        "-c", 'approval_policy="never"',        # never stop to ask; anything risky simply fails
        "-c", "sandbox_workspace_write.network_access=false",  # no internet, no installs
        "-c", f'model_reasoning_effort="{config.FIX_REASONING_EFFORT}"',  # think briefly: fast on stage
        "--ephemeral",                          # don't keep session files
        "--color", "never",
        "--output-schema", str(schema_file),    # answer as JSON in ANSWER_SCHEMA's shape
        "-o", str(answer_file),                 # write that final answer to a file for us
        prompt,
    ]


def _stop_process(process: subprocess.Popen) -> None:
    """Stop Codex and anything it started (it runs in its own process group)."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_codex(
    failure: LastFailure,
    root: Path,
    cancel: threading.Event,
    on_tick: Callable[[float], None] | None = None,
    log_file: Path | None = None,
) -> CodexAnswer:
    """Run `codex exec` once and return its answer. Raises FixRefused on timeout, cancel or error.

    on_tick(seconds_so_far) is called about twice a second, so the caller can play
    ticks or say "still on it" while Codex works.
    """
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as scratch:
        schema_file = Path(scratch) / "answer_schema.json"
        answer_file = Path(scratch) / "answer.json"
        schema_file.write_text(json.dumps(ANSWER_SCHEMA), encoding="utf-8")
        prompt = PROMPT.format(command=failure.command or "(unknown)")
        command = codex_command(root, schema_file, answer_file, prompt)
        error_text = failure.output[-config.FIX_MAX_ERROR_CHARS:]

        log_path = log_file or Path(scratch) / "codex.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,  # own process group, so cancel stops all of it
                )
            except OSError as error:
                raise FixRefused(f"Codex could not start: {error}")
            # The error goes in through stdin; Codex adds it to the prompt.
            process.stdin.write(error_text)
            process.stdin.close()

            while process.poll() is None:
                elapsed = time.monotonic() - started
                if cancel.is_set():
                    _stop_process(process)
                    raise FixRefused("Fix cancelled.")
                if elapsed > config.FIX_TIMEOUT_SECONDS:
                    _stop_process(process)
                    raise FixRefused("Codex ran out of time.")
                if on_tick:
                    on_tick(elapsed)
                cancel.wait(0.5)

        if process.returncode != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-300:].strip()
            raise FixRefused(f"Codex stopped with an error. {tail}")
        try:
            data = json.loads(answer_file.read_text(encoding="utf-8"))
            return CodexAnswer(
                fixed=bool(data["fixed"]), summary=str(data["summary"]).strip(), verified=bool(data["verified"])
            )
        except (OSError, ValueError, KeyError) as error:
            raise FixRefused(f"Codex finished, but its answer was unreadable ({error}).")


# ---------------------------------------------------------------------------
# The Fix job: everything that happens after you ask for a fix.
# ---------------------------------------------------------------------------

DIFF_PATH = Path.home() / ".murmur" / "last_fix.diff"  # the last fix's exact change, to read in full
LOG_PATH = Path.home() / ".murmur" / "last_fix_codex.log"  # what Codex printed, for debugging


@dataclass(frozen=True)
class LastFix:
    """What Undo needs: the project and its snapshots from before and after the fix."""

    root: Path
    before: str
    after: str
    files: list[str]  # the real files Codex changed (cache files left out)


_LAST_FIX: LastFix | None = None


def remember_fix(fix: LastFix | None) -> None:
    global _LAST_FIX
    with _LOCK:
        _LAST_FIX = fix


def last_fix() -> LastFix | None:
    with _LOCK:
        return _LAST_FIX


def _say(text: str) -> None:
    from murmur import voice  # imported here so tests of the safety bits never load audio

    print(f"Murmur says: {text}")
    voice.speak(text, mode=personality.get_mode())


def _tick() -> None:
    from murmur import voice

    voice.play_soft_sound()


class _Progress:
    """Keeps you company while Codex works.

    Right away: a short "working on it" line (Codex has already started, so no time is lost).
    Every FIX_TICK_SECONDS: a quiet tick.
    Once, after FIX_STILL_ON_IT_SECONDS: one short "still on it" line instead of that tick.
    """

    def __init__(self, cancel: threading.Event, say: Callable[[str], None], tick: Callable[[], None]):
        self.cancel = cancel
        self.say = say
        self.tick = tick
        self.cue_said = False
        self.still_said = False
        self.next_tick = config.FIX_TICK_SECONDS

    def __call__(self, elapsed: float) -> None:
        if self.cancel.is_set():
            return
        if not self.cue_said:
            self.cue_said = True
            self.say(personality.fix_line("working"))
        elif not self.still_said and elapsed >= config.FIX_STILL_ON_IT_SECONDS:
            self.still_said = True
            self.say(personality.fix_line("still"))
            self.next_tick = elapsed + config.FIX_TICK_SECONDS
        elif elapsed >= self.next_tick:
            self.tick()
            self.next_tick += config.FIX_TICK_SECONDS


def _file_list(paths: list[str]) -> str:
    """Up to three plain file names, like "shop.py, cart.py and tax.py and 2 more"."""
    names = [Path(path).name for path in paths]
    shown = names[:3]
    text = shown[0] if len(shown) == 1 else f"{', '.join(shown[:-1])} and {shown[-1]}"
    if len(names) > 3:
        text += f" and {len(names) - 3} more"
    return text


def spoken_fix_summary(files: list[str], answer: CodexAnswer) -> str:
    """What Murmur says after a fix: opener, files, Codex's sentence, and how to undo."""
    parts = [personality.fix_line("fixed"), f"Changed {_file_list(files)}.", answer.summary]
    if answer.verified:
        parts.append("The command passes now.")
    parts.append("Undo last fix is in the Murmur menu.")
    return " ".join(part for part in parts if part)


def _roll_back(root: Path, before: str) -> bool:
    """Put back anything Codex half-changed. True if the project is as it was."""
    try:
        undo(root, before, snapshot(root))
        return True
    except FixRefused as error:
        print(f"Murmur Fix it: could not roll back ({error})")
        return False


def fix_last_failure(
    cancel: threading.Event,
    *,
    say: Callable[[str], None] = _say,
    tick: Callable[[], None] = _tick,
    runner: Callable[..., CodexAnswer] = run_codex,
    ready: Callable[[], str | None] = codex_ready,
) -> LastFix | None:
    """Ask Codex to fix the last failure, then say what changed. Returns the fix, or None."""
    print("----- Murmur: Fix it -----")
    try:
        return _fix(cancel, say, tick, runner, ready)
    finally:
        print("----- End of Murmur Fix it -----")


def _fix(cancel, say, tick, runner, ready) -> LastFix | None:
    failure = last_failure()
    if failure is None:
        say("Nothing to fix. The last command didn't fail.")
        return None
    problem = ready()
    if problem:
        say(problem)
        return None
    try:
        root = project_root(failure.folder)
        before = snapshot(root)
    except FixRefused as error:
        say(f"{personality.fix_line('not_fixed')} {error}")
        return None

    print(f"Project: {root}")
    print(f"Command that failed: {failure.command or '(unknown)'}")
    started = time.monotonic()
    try:
        answer = runner(failure, root, cancel, on_tick=_Progress(cancel, say, tick), log_file=LOG_PATH)
    except FixRefused as error:
        print(f"Fix took {time.monotonic() - started:.1f} s.")
        restored = _roll_back(root, before)
        if not cancel.is_set():  # if you cancelled, you already know; stay quiet
            ending = "Nothing was changed." if restored else "Some files may have changed; check git status."
            say(f"{personality.fix_line('not_fixed')} {error} {ending}")
        print(f"(stopped: {error})")
        return None

    print(f"Fix took {time.monotonic() - started:.1f} s.")
    after = snapshot(root)
    files = [path for _, path in changed_files(root, before, after) if not is_junk(path)]
    print(f"Codex says: {answer.summary}")

    if not answer.fixed or not files:
        # Codex didn't find a safe fix. Put back anything it touched, so you get a clean slate.
        _roll_back(root, before)
        reason = answer.summary or "Codex didn't change any files."
        say(f"{personality.fix_line('not_fixed')} {reason} Nothing was changed.")
        return None

    diff = diff_text(root, before, after, files)
    print("----- Murmur: the exact change -----")
    print(diff.rstrip())
    try:
        DIFF_PATH.parent.mkdir(parents=True, exist_ok=True)
        DIFF_PATH.write_text(diff, encoding="utf-8")
        print(f"(Saved to {DIFF_PATH})")
    except OSError as error:
        print(f"(Could not save the diff to {DIFF_PATH}: {error})")

    fix = LastFix(root=root, before=before, after=after, files=files)
    remember_fix(fix)
    forget_failure()  # it's fixed now; don't let Fix it go after the same error twice
    say(spoken_fix_summary(files, answer))
    return fix
