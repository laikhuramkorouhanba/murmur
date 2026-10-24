from murmur.monitor import CompletionDetector, FinishedCommand
from murmur.watcher import TabSnapshot

SHELL = ("login", "-zsh")


def snap(busy: bool, tty: str = "/dev/ttys001", extra: tuple[str, ...] = ()) -> TabSnapshot:
    return TabSnapshot(tty=tty, busy=busy, processes=SHELL + extra)


def feed(detector: CompletionDetector, snapshots, start: float = 0.0, step: float = 1.0):
    """Feed one snapshot per second and collect whatever the detector reports."""
    results = []
    for index, snapshot in enumerate(snapshots):
        result = detector.update(snapshot, start + index * step)
        if result:
            results.append(result)
    return results


def test_busy_then_idle_is_a_finished_command_with_its_duration() -> None:
    detector = CompletionDetector()

    results = feed(detector, [snap(False), snap(True), snap(True), snap(True), snap(False)])

    assert results == [FinishedCommand(tty="/dev/ttys001", seconds=3.0)]


def test_staying_idle_or_busy_reports_nothing() -> None:
    detector = CompletionDetector()

    assert feed(detector, [snap(False)] * 5) == []
    assert feed(detector, [snap(True)] * 5, start=10) == []


def test_switching_from_a_busy_tab_to_an_idle_tab_is_not_finished() -> None:
    detector = CompletionDetector()

    results = feed(detector, [snap(True), snap(True), snap(False, tty="/dev/ttys002")])

    assert results == []


def test_switching_back_after_the_old_tab_finished_reports_nothing() -> None:
    detector = CompletionDetector()

    results = feed(
        detector,
        [snap(True), snap(False, tty="/dev/ttys002"), snap(False), snap(False)],
    )

    assert results == []


def test_a_tab_already_busy_when_you_switch_to_it_is_still_announced_when_it_ends() -> None:
    detector = CompletionDetector()

    results = feed(
        detector,
        [snap(False), snap(True, tty="/dev/ttys002"), snap(True, tty="/dev/ttys002"), snap(False, tty="/dev/ttys002")],
    )

    assert results == [FinishedCommand(tty="/dev/ttys002", seconds=2.0)]


def test_leaving_vim_is_not_a_finished_command() -> None:
    detector = CompletionDetector()

    results = feed(
        detector,
        [snap(False), snap(True, extra=("vim",)), snap(True, extra=("vim",)), snap(False)],
    )

    assert results == []


def test_vim_opening_halfway_through_ignores_the_whole_run() -> None:
    detector = CompletionDetector()

    # Like "git commit": it runs git first, then opens vim for the message.
    results = feed(
        detector,
        [snap(True, extra=("git",)), snap(True, extra=("git", "/usr/bin/vim")), snap(False)],
    )

    assert results == []


def test_the_run_after_an_ignored_one_is_announced_again() -> None:
    detector = CompletionDetector()

    results = feed(
        detector,
        [snap(True, extra=("less",)), snap(False), snap(True, extra=("pytest",)), snap(True), snap(False)],
    )

    assert results == [FinishedCommand(tty="/dev/ttys001", seconds=2.0)]


def test_terminal_closing_mid_run_reports_nothing() -> None:
    detector = CompletionDetector()

    assert feed(detector, [snap(True), None, snap(False)]) == []
