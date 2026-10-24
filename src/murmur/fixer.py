"""Fix it: ask Codex to make the smallest fix for the last failing command.

This is where Murmur uses OpenAI Codex. The whole flow:
1. Murmur remembers the last failure it announced (the error text and the project folder).
2. On "Fix it", it takes a snapshot of the project so every change can be undone.
3. It runs `codex exec` in that project, sandboxed, with the error as context.
4. It compares the project before and after, prints the exact diff, and speaks a short summary.
5. "Undo last fix" puts the files back from the snapshot.
"""

import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path


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
