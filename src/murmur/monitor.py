"""Spot the moment a command finishes in Terminal's front tab.

This file only does the thinking. It gets a snapshot of the tab about once a
second and answers "did a command just finish?". No threads or AppleScript here,
so it is easy to test with a pretend clock.
"""

from dataclasses import dataclass

from murmur import config
from murmur.watcher import TabSnapshot


@dataclass(frozen=True)
class FinishedCommand:
    """A command that just finished in the tab with this tty."""

    tty: str
    seconds: float  # roughly how long it ran, give or take one check


def _program_name(process: str) -> str:
    # Terminal lists processes like "-zsh" or "/usr/bin/vim"; keep just "zsh" or "vim".
    return process.rsplit("/", 1)[-1].lstrip("-").lower()


class CompletionDetector:
    """Watch one tab go from busy to not busy, and report that as a finished command."""

    def __init__(self, ignored_programs: frozenset[str] = config.AUTO_IGNORED_PROGRAMS) -> None:
        self._ignored = ignored_programs
        self._tty: str | None = None
        self._busy_since: float | None = None
        self._ignore_this_run = False

    def _start_watching(self, snapshot: TabSnapshot | None, now: float) -> None:
        self._tty = snapshot.tty if snapshot else None
        self._busy_since = now if snapshot and snapshot.busy else None
        self._ignore_this_run = bool(snapshot and snapshot.busy and self._is_ignored(snapshot))

    def _is_ignored(self, snapshot: TabSnapshot) -> bool:
        return any(_program_name(process) in self._ignored for process in snapshot.processes)

    def update(self, snapshot: TabSnapshot | None, now: float) -> FinishedCommand | None:
        """Feed in the latest snapshot. Returns a FinishedCommand only at the moment one ends."""
        # Terminal closed, or you switched to another tab: start fresh, announce nothing.
        # Without this, switching from a busy tab to an idle one would look like "finished".
        if snapshot is None or snapshot.tty != self._tty:
            self._start_watching(snapshot, now)
            return None

        if snapshot.busy:
            if self._busy_since is None:
                self._busy_since = now
            # Once vim or less shows up during a run, that whole run is ignored.
            if self._is_ignored(snapshot):
                self._ignore_this_run = True
            return None

        if self._busy_since is None:
            return None  # still idle, nothing new

        finished = FinishedCommand(tty=snapshot.tty, seconds=now - self._busy_since)
        ignored = self._ignore_this_run
        self._busy_since = None
        self._ignore_this_run = False
        return None if ignored else finished
