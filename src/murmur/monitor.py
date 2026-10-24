"""Spot the moment a command finishes in Terminal's front tab.

This file only does the thinking. It gets a snapshot of the tab about once a
second and answers "did a command just finish?". No threads or AppleScript here,
so it is easy to test with a pretend clock.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from murmur import config, settings, watcher
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


class AutoAnnouncer:
    """Peek at Terminal about once a second on a background thread.

    It never touches the menu bar's main thread, so the menu stays smooth. When
    a command finishes, it calls on_finished. Problems are printed once, then it
    quietly checks every 5 seconds until Terminal can be reached again.
    """

    def __init__(
        self,
        on_finished: Callable[[FinishedCommand], None],
        *,
        snapshot: Callable[[], TabSnapshot | None] = watcher.tab_snapshot,
        enabled: Callable[[], bool] = lambda: bool(settings.get("auto_announce")),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_finished = on_finished
        self._snapshot = snapshot
        self._enabled = enabled
        self._clock = clock
        self._detector = CompletionDetector()
        self._last_problem: str | None = None
        self._stop = threading.Event()

    def check_once(self) -> float:
        """Do one peek. Returns how many seconds to wait before the next one."""
        if not self._enabled():
            # Forget any half-watched command, so turning it back on starts clean.
            self._detector.update(None, self._clock())
            return config.AUTO_POLL_SECONDS

        try:
            snapshot = self._snapshot()
        except RuntimeError as error:
            self._detector.update(None, self._clock())
            if str(error) != self._last_problem:
                self._last_problem = str(error)
                print(f"Murmur auto-announce paused: {error} Checking again every 5 seconds.")
            return config.AUTO_ERROR_POLL_SECONDS

        if self._last_problem is not None:
            self._last_problem = None
            print("Murmur auto-announce: Terminal is reachable again.")

        finished = self._detector.update(snapshot, self._clock())
        if finished is not None:
            self._on_finished(finished)
        return config.AUTO_POLL_SECONDS

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                delay = self.check_once()
            except Exception as error:  # never let one odd answer kill the watcher
                print(f"Murmur auto-announce hit a problem and keeps going: {error}")
                delay = config.AUTO_ERROR_POLL_SECONDS
            self._stop.wait(delay)

    def start(self) -> None:
        threading.Thread(target=self._run, name="murmur-auto-announce", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
