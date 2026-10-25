"""Commands that finish between two busy checks (found in the M5 live test).

`uv run pytest -q shop_demo_check.py` failed in under a second, so Terminal
never looked busy when Murmur asked, and auto-announce stayed quiet.
"""

import pytest

from murmur import config, watcher
from murmur.monitor import AutoAnnouncer, FinishedCommand, QuickCommandSpotter
from murmur.watcher import TabSnapshot

TTY = "/dev/ttys005"
PROMPT = "(murmur) ada@macbook murmur-fixme-demo %"
BEFORE = f"{PROMPT} ls\nshop.py\n{PROMPT} "
AFTER_FAIL = (
    f"{BEFORE.rstrip()} uv run pytest -q shop_demo_check.py\n"
    "F    [100%]\nFAILED shop_demo_check.py::test_order_total_adds_sales_tax\n1 failed in 0.10s\n"
    f"{PROMPT} "
)


# --- reading the screen ------------------------------------------------------


@pytest.mark.parametrize(
    "screen, command",
    [
        (AFTER_FAIL, "uv run pytest -q shop_demo_check.py"),
        (AFTER_FAIL + "\n\n\n", "uv run pytest -q shop_demo_check.py"),  # Terminal pads the screen
        (f"{PROMPT} uv run pyt", None),  # still typing
        (f"{PROMPT} ", None),  # a cleared screen
        (f"{PROMPT} ls\nshop.py\n{PROMPT}\n{PROMPT} ", None),  # Enter on an empty prompt
        ("Building... 40%", None),  # still running, no prompt yet
    ],
)
def test_a_finished_command_is_a_command_line_then_a_fresh_prompt(screen, command) -> None:
    assert watcher.finished_command_on_screen(screen) == command


def test_tty_output_time_refuses_odd_names() -> None:
    assert watcher.tty_output_time("/etc/passwd") is None


# --- the spotter ----------------------------------------------------------------


def run_ticks(spotter: QuickCommandSpotter, ticks, screens):
    """ticks: (now, busy, output_time). screens: what the screen shows when it's read, in order."""
    found = []
    for now, busy, output_time in ticks:
        if spotter.wants_to_look(TTY, busy, output_time, now):
            result = spotter.look(screens.pop(0), now)
            if result:
                found.append(result)
    return found


def test_a_quick_failure_is_spotted_once_output_settles() -> None:
    spotter = QuickCommandSpotter()
    ticks = [(0.0, False, 100.0), (0.25, False, 100.0), (0.5, False, 101.0), (0.75, False, 101.0),
             (1.0, False, 101.0), (1.25, False, 101.0), (1.5, False, 101.0)]

    found = run_ticks(spotter, ticks, [BEFORE, AFTER_FAIL])

    assert found == [FinishedCommand(tty=TTY, seconds=0.0)]


def test_the_first_look_at_a_tab_never_reports_old_commands() -> None:
    spotter = QuickCommandSpotter()

    assert run_ticks(spotter, [(0.0, False, 100.0)], [AFTER_FAIL]) == []


def test_typing_is_not_a_finished_command() -> None:
    spotter = QuickCommandSpotter()
    ticks = [(0.0, False, 100.0), (0.5, False, 100.5), (1.25, False, 100.5)]

    assert run_ticks(spotter, ticks, [BEFORE, f"{BEFORE}uv run pyt"]) == []


def test_nothing_is_read_while_the_tab_is_quiet() -> None:
    spotter = QuickCommandSpotter()
    screens = [BEFORE]

    run_ticks(spotter, [(t / 4, False, 100.0) for t in range(40)], screens)

    assert screens == []  # one baseline look, then nothing for 10 seconds


def test_a_command_the_busy_check_already_reported_is_not_reported_again() -> None:
    spotter = QuickCommandSpotter()
    run_ticks(spotter, [(0.0, False, 100.0)], [BEFORE])
    spotter.busy_check_finished_one(5.0)

    found = run_ticks(spotter, [(5.0, False, 101.0), (5.75, False, 101.0)], [AFTER_FAIL])

    assert found == []


def test_leaving_vim_is_not_reported() -> None:
    spotter = QuickCommandSpotter()
    after_vim = f"{BEFORE.rstrip()} vim shop.py\n{PROMPT} "

    found = run_ticks(spotter, [(0.0, False, 100.0), (1.0, False, 101.0), (1.75, False, 101.0)], [BEFORE, after_vim])

    assert found == []


# --- the whole announcer, with busy never seen --------------------------------------


class FakeTerminal:
    """A tab that never looks busy, and a tty clock we can move."""

    def __init__(self) -> None:
        self.now = 0.0
        self.output_time = 100.0
        self.screen = BEFORE
        self.screen_reads = 0

    def clock(self) -> float:
        self.now += config.AUTO_POLL_SECONDS
        return self.now

    def snapshot(self) -> TabSnapshot:
        return TabSnapshot(tty=TTY, busy=False, processes=("login", "-zsh"))

    def read_screen(self, tty: str) -> str:
        assert tty == TTY
        self.screen_reads += 1
        return self.screen


def test_regression_a_sub_second_failure_is_auto_announced_once() -> None:
    terminal = FakeTerminal()
    finished = []
    announcer = AutoAnnouncer(
        finished.append,
        snapshot=terminal.snapshot,
        enabled=lambda: True,
        clock=terminal.clock,
        output_time=lambda tty: terminal.output_time,
        screen=terminal.read_screen,
    )
    for _ in range(8):
        announcer.check_once()

    # The quick test runs and fails between two busy checks.
    terminal.output_time, terminal.screen = 101.0, AFTER_FAIL
    for _ in range(20):
        announcer.check_once()

    assert finished == [FinishedCommand(tty=TTY, seconds=0.0)]
    assert terminal.screen_reads == 2  # one baseline, one after the output settled


def test_screen_script_counts_tabs_by_number() -> None:
    # "repeat with theTab in tabs" + "contents of theTab" returns the tab, not its text.
    script = watcher._tab_script(TTY, "contents")

    assert "contents of tab t of window w" in script
    assert "in tabs" not in script
