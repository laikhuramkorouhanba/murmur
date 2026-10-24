import subprocess

import pytest

from murmur import watcher


def completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_reads_front_selected_tab_history(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        if command[2] == watcher._RUNNING_SCRIPT:
            return completed(stdout="true\n")
        return completed(stdout="$ pytest\n1 failed\n")

    monkeypatch.setattr(watcher.subprocess, "run", fake_run)

    assert watcher.get_terminal_text() == "$ pytest\n1 failed\n"
    assert calls[0][0] == ["osascript", "-e", 'application "Terminal" is running']
    assert calls[0][1]["timeout"] == 5
    assert calls[1][0][:2] == ["osascript", "-e"]
    assert "history of selected tab of front window" in calls[1][0][2]
    assert calls[1][1]["timeout"] == 5


def test_explains_when_terminal_is_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess, "run", lambda *args, **kwargs: completed(stdout="false\n")
    )

    with pytest.raises(RuntimeError, match="Terminal.app is not running"):
        watcher.get_terminal_text()


def test_reports_unrecognized_running_check_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess, "run", lambda *args, **kwargs: completed(stdout="maybe\n")
    )

    with pytest.raises(RuntimeError) as error:
        watcher.get_terminal_text()

    assert str(error.value) == (
        "Could not determine whether Terminal.app is running; "
        "osascript returned 'maybe\\n'."
    )


def test_explains_when_terminal_has_no_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        lambda command, **kwargs: completed(stdout="true\n")
        if command[2] == watcher._RUNNING_SCRIPT
        else completed(stdout=watcher._NO_WINDOWS),
    )

    with pytest.raises(RuntimeError, match="has no windows open"):
        watcher.get_terminal_text()


def test_explains_automation_permission_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        lambda command, **kwargs: completed(stdout="true\n")
        if command[2] == watcher._RUNNING_SCRIPT
        else completed(1, stderr="Not authorized to send Apple events (-1743)"),
    )

    with pytest.raises(RuntimeError, match="System Settings"):
        watcher.get_terminal_text()


def test_automation_permission_check_only_flags_macos_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        lambda *args, **kwargs: completed(1, stderr="Not authorized to send Apple events (-1743)"),
    )

    assert watcher.automation_permission_missing()

    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        lambda *args, **kwargs: completed(stdout="true\n"),
    )
    assert not watcher.automation_permission_missing()

    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        lambda *args, **kwargs: completed(stdout="false\n"),
    )
    assert not watcher.automation_permission_missing()


def test_explains_osascript_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[2] == watcher._RUNNING_SCRIPT:
            return completed(stdout="true\n")
        if command[0] == "osascript":
            raise subprocess.TimeoutExpired(command, 5)
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(watcher.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="read Terminal.app history after 5 seconds"):
        watcher.get_terminal_text()


def test_snapshot_reads_tty_busy_and_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return completed(stdout="/dev/ttys003\ttrue\tlogin,-zsh,python3\n")

    monkeypatch.setattr(watcher.subprocess, "run", fake_run)

    assert watcher.tab_snapshot() == watcher.TabSnapshot(
        tty="/dev/ttys003", busy=True, processes=("login", "-zsh", "python3")
    )
    assert len(calls) == 1  # one cheap call, not two
    assert 'if application "Terminal" is not running' in calls[0][2]


@pytest.mark.parametrize("answer", ["MURMUR_NOT_RUNNING", "MURMUR_NO_WINDOWS", "MURMUR_NO_TAB", ""])
def test_snapshot_is_none_when_there_is_nothing_to_watch(monkeypatch, answer) -> None:
    monkeypatch.setattr(watcher.subprocess, "run", lambda *a, **k: completed(stdout=answer + "\n"))

    assert watcher.tab_snapshot() is None


def test_snapshot_complains_about_a_strange_answer(monkeypatch) -> None:
    monkeypatch.setattr(watcher.subprocess, "run", lambda *a, **k: completed(stdout="what\n"))

    with pytest.raises(RuntimeError, match="Could not understand"):
        watcher.tab_snapshot()


def test_snapshot_passes_on_the_permission_message(monkeypatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        lambda *a, **k: completed(returncode=1, stderr="Not authorized to send Apple events (-1743)"),
    )

    with pytest.raises(RuntimeError, match="Privacy & Security → Automation"):
        watcher.tab_snapshot()


def test_reads_a_specific_tab_by_its_tty(monkeypatch) -> None:
    scripts = []
    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        lambda command, **k: (scripts.append(command[2]) or completed(stdout="% make\nok\n")),
    )

    assert watcher.get_tab_text("/dev/ttys007") == "% make\nok\n"
    assert 'if tty of theTab is "/dev/ttys007"' in scripts[0]


def test_closed_tab_gives_a_clear_message(monkeypatch) -> None:
    monkeypatch.setattr(watcher.subprocess, "run", lambda *a, **k: completed(stdout="MURMUR_NO_TAB\n"))

    with pytest.raises(RuntimeError, match="closed before Murmur could read it"):
        watcher.get_tab_text("/dev/ttys007")


def test_refuses_an_odd_tty_instead_of_putting_it_in_applescript() -> None:
    with pytest.raises(RuntimeError, match="unexpected Terminal tab name"):
        watcher.get_tab_text('/dev/ttys1" & do shell script "rm')
