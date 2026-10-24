"""Fix it: ask Codex to make the smallest fix for the last failing command.

This is where Murmur uses OpenAI Codex. The whole flow:
1. Murmur remembers the last failure it announced (the error text and the project folder).
2. On "Fix it", it takes a snapshot of the project so every change can be undone.
3. It runs `codex exec` in that project, sandboxed, with the error as context.
4. It compares the project before and after, prints the exact diff, and speaks a short summary.
5. "Undo last fix" puts the files back from the snapshot.
"""

import threading
from dataclasses import dataclass


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
