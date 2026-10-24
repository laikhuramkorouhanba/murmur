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

from murmur import config


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


def diff_text(root: Path, before: str, after: str) -> str:
    """The exact change, line by line, the same as `git diff` shows."""
    return _git(root, "diff", "--no-color", before, after)


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
