"""Spot the moment a command finishes in Terminal's front tab.

Two ways, because one isn't enough:
- CompletionDetector asks Terminal "is the tab busy?" once a second and notices
  busy turning into not busy. Great for slow commands, but a command that's over
  in half a second can start and end between two checks, unseen.
- QuickCommandSpotter catches those. It watches the tab's tty for new output (a
  free file check, four times a second). When the output settles, it looks at the
  screen once: a command line, then output, then a fresh empty prompt means a
  command just finished.

The thinking lives in plain classes with a pretend clock, so it's easy to test.
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

    def forget_run(self, now: float) -> float:
        """The screen check already reported this command. Drop it, and say how long it ran."""
        seconds = now - self._busy_since if self._busy_since is not None else 0.0
        self._busy_since = None
        self._ignore_this_run = False
        return seconds


class QuickCommandSpotter:
    """Catch commands too quick for the busy check, by looking at the screen once output settles."""

    def __init__(self, ignored_programs: frozenset[str] = config.AUTO_IGNORED_PROGRAMS) -> None:
        self._ignored = ignored_programs
        self.reset()
        self._last_busy_finish = float("-inf")

    def reset(self) -> None:
        self._tty: str | None = None
        self._output_time: float | None = None
        self._look_at: float | None = None  # when to look at the screen next
        self._screen: str | None = None  # what the screen showed last time we looked

    def busy_check_finished_one(self, now: float) -> None:
        """The busy check just reported a command, so the screen check must not report it again."""
        self._last_busy_finish = now

    def wants_to_look(self, tty: str, busy: bool, output_time: float | None, now: float) -> bool:
        """Called every tick. True means "read the screen now, please"."""
        if output_time is None:
            self.reset()
            return False
        if tty != self._tty:
            # A different tab: remember what it looks like now, but don't report anything old.
            self.reset()
            self._tty, self._output_time = tty, output_time
            return True
        if output_time != self._output_time:
            # Something was printed. Wait until it goes quiet, so we look once, not mid-stream.
            self._output_time = output_time
            self._look_at = now + config.AUTO_QUIET_SECONDS
            return False
        if busy or self._look_at is None or now < self._look_at:
            return False
        self._look_at = None
        return True

    def look(self, screen: str, now: float) -> FinishedCommand | None:
        """Read the screen we were given. Returns a FinishedCommand if one just appeared."""
        screen = screen.rstrip()
        first_look = self._screen is None
        changed = screen != self._screen
        self._screen = screen
        if first_look or not changed or self._tty is None:
            return None
        if now - self._last_busy_finish < config.AUTO_SAME_FINISH_SECONDS:
            return None  # the busy check already got this one
        command = watcher.finished_command_on_screen(screen)
        if not command or _program_name(command.split()[0]) in self._ignored:
            return None
        return FinishedCommand(tty=self._tty, seconds=0.0)


class AutoAnnouncer:
    """Watch Terminal on a background thread: the busy check once a second, the tty check more often.

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
        output_time: Callable[[str], float | None] | None = None,
        screen: Callable[[str], str] | None = None,
    ) -> None:
        self._on_finished = on_finished
        self._snapshot = snapshot
        self._enabled = enabled
        self._clock = clock
        # Looked up when used (not here), so tests can swap the watcher's real ones out.
        self._output_time = output_time or (lambda tty: watcher.tty_output_time(tty))
        self._screen = screen or (lambda tty: watcher.get_tab_contents(tty))
        self._detector = CompletionDetector()
        self._spotter = QuickCommandSpotter()
        self._latest: TabSnapshot | None = None
        self._next_snapshot = float("-inf")
        self._last_problem: str | None = None
        self._stop = threading.Event()

    def check_once(self) -> float:
        """Do one tick. Returns how many seconds to wait before the next one."""
        now = self._clock()
        if not self._enabled():
            # Forget any half-watched command, so turning it back on starts clean.
            self._detector.update(None, now)
            self._spotter.reset()
            self._latest = None
            return config.AUTO_POLL_SECONDS

        if now >= self._next_snapshot:
            self._next_snapshot = now + config.AUTO_SNAPSHOT_SECONDS
            try:
                snapshot = self._snapshot()
            except RuntimeError as error:
                self._detector.update(None, now)
                self._spotter.reset()
                self._latest = None
                self._next_snapshot = now  # try again on the next (slower) tick
                if str(error) != self._last_problem:
                    self._last_problem = str(error)
                    print(f"Murmur auto-announce paused: {error} Checking again every 5 seconds.")
                return config.AUTO_ERROR_POLL_SECONDS

            if self._last_problem is not None:
                self._last_problem = None
                print("Murmur auto-announce: Terminal is reachable again.")

            self._latest = snapshot
            finished = self._detector.update(snapshot, now)
            if finished is not None:
                self._spotter.busy_check_finished_one(now)
                self._on_finished(finished)
                return config.AUTO_POLL_SECONDS

        self._check_screen(now)
        return config.AUTO_POLL_SECONDS

    def _check_screen(self, now: float) -> None:
        """The quick-command check: free tty peek, and a screen read only when output settles."""
        latest = self._latest
        if latest is None:
            return
        if not self._spotter.wants_to_look(latest.tty, latest.busy, self._output_time(latest.tty), now):
            return
        try:
            screen = self._screen(latest.tty)
        except RuntimeError:
            return  # the tab closed; the next busy check sorts it out
        quick = self._spotter.look(screen, now)
        if quick is not None:
            seconds = self._detector.forget_run(now)  # so the busy check won't report it again
            self._on_finished(FinishedCommand(tty=quick.tty, seconds=seconds))

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
