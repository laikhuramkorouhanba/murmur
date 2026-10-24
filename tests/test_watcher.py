import subprocess

import pytest

from murmur import watcher


def completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_reads_front_selected_tab_history(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        if command[0] == "pgrep":
            return completed()
        return completed(stdout="$ pytest\n1 failed\n")

    monkeypatch.setattr(watcher.subprocess, "run", fake_run)

    assert watcher.get_terminal_text() == "$ pytest\n1 failed\n"
    assert calls[0][0] == ["pgrep", "-x", "Terminal"]
    assert calls[1][0][:2] == ["osascript", "-e"]
    assert "contents of selected tab of front window" in calls[1][0][2]
    assert calls[1][1]["timeout"] == 5


def test_explains_when_terminal_is_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watcher.subprocess, "run", lambda *args, **kwargs: completed(1))

    with pytest.raises(RuntimeError, match="Terminal.app is not running"):
        watcher.get_terminal_text()


def test_explains_when_terminal_has_no_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        lambda command, **kwargs: completed(stdout=watcher._NO_WINDOWS)
        if command[0] == "osascript"
        else completed(),
    )

    with pytest.raises(RuntimeError, match="has no windows open"):
        watcher.get_terminal_text()


def test_explains_automation_permission_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watcher.subprocess,
        "run",
        lambda command, **kwargs: completed(1, stderr="Not authorized to send Apple events (-1743)")
        if command[0] == "osascript"
        else completed(),
    )

    with pytest.raises(RuntimeError, match="System Settings"):
        watcher.get_terminal_text()


def test_explains_osascript_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "osascript":
            raise subprocess.TimeoutExpired(command, 5)
        return completed()

    monkeypatch.setattr(watcher.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="longer than 5 seconds"):
        watcher.get_terminal_text()
