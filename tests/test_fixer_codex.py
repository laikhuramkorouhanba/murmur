import json
import threading
from pathlib import Path

import pytest

from murmur import config, fixer

FAILURE = fixer.LastFailure(
    tty="/dev/ttys004",
    folder="/tmp/shop",
    command="pytest -q",
    output="checkout.py:12: SyntaxError: '(' was never closed",
)


def test_the_codex_command_is_sandboxed_quiet_and_quick(tmp_path: Path) -> None:
    command = fixer.codex_command(tmp_path, tmp_path / "s.json", tmp_path / "a.json", "PROMPT")

    assert command[:2] == ["codex", "exec"]
    assert command[command.index("--cd") + 1] == str(tmp_path)
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    settings = [command[i + 1] for i, part in enumerate(command) if part == "-c"]
    assert 'approval_policy="never"' in settings
    assert "sandbox_workspace_write.network_access=false" in settings
    assert 'model_reasoning_effort="low"' in settings
    assert "--ephemeral" in command
    assert command[command.index("--output-schema") + 1] == str(tmp_path / "s.json")
    assert command[command.index("-o") + 1] == str(tmp_path / "a.json")
    assert command[-1] == "PROMPT"
    assert not any("dangerously" in part for part in command)
    assert "--add-dir" not in command


class FakeProcess:
    """Pretends to be Codex: finishes after a few polls and writes an answer file."""

    def __init__(self, command, *, polls=2, returncode=0, answer=None, env=None, **_kwargs):
        self.command = command
        self.env = env
        self.pid = 4242
        self.polls_left = polls
        self.returncode = None
        self._final = returncode
        self.stdin_text = ""
        self.stdin = self
        answer_path = Path(command[command.index("-o") + 1])
        if answer is not None:
            answer_path.write_text(json.dumps(answer), encoding="utf-8")

    def write(self, text):
        self.stdin_text += text

    def close(self):
        pass

    def poll(self):
        if self.polls_left <= 0:
            self.returncode = self._final
            return self.returncode
        self.polls_left -= 1
        return None

    def wait(self, timeout=None):
        return self.returncode


def install(monkeypatch, **behaviour):
    made = []

    def popen(command, **kwargs):
        process = FakeProcess(command, **behaviour, **kwargs)
        made.append(process)
        return process

    monkeypatch.setattr(fixer.subprocess, "Popen", popen)
    killed = []
    monkeypatch.setattr(fixer.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    no_wait = threading.Event()
    monkeypatch.setattr(no_wait, "wait", lambda seconds: None)
    return made, killed, no_wait


def test_a_good_answer_is_read_back(monkeypatch, tmp_path) -> None:
    answer = {"fixed": True, "summary": "Closed the bracket on line 12 of checkout.py.", "verified": True}
    made, _, cancel = install(monkeypatch, answer=answer)
    ticks = []

    result = fixer.run_codex(FAILURE, tmp_path, cancel, on_tick=ticks.append)

    assert result == fixer.CodexAnswer(True, "Closed the bracket on line 12 of checkout.py.", True)
    assert "The failing command was: pytest -q" in made[0].command[-1]
    assert made[0].stdin_text == FAILURE.output  # the error goes in through stdin
    assert len(ticks) == 2


def test_cancel_stops_codex_and_its_children(monkeypatch, tmp_path) -> None:
    made, killed, cancel = install(monkeypatch, polls=100)
    cancel.set()

    with pytest.raises(fixer.FixRefused, match="Fix cancelled"):
        fixer.run_codex(FAILURE, tmp_path, cancel)

    assert killed and killed[0][0] == 4242


def test_timeout_stops_codex(monkeypatch, tmp_path) -> None:
    made, killed, cancel = install(monkeypatch, polls=100)
    clock = iter([0.0, 0.0, config.FIX_TIMEOUT_SECONDS + 1])
    monkeypatch.setattr(fixer.time, "monotonic", lambda: next(clock))

    with pytest.raises(fixer.FixRefused, match="ran out of time"):
        fixer.run_codex(FAILURE, tmp_path, cancel)

    assert killed


def test_codex_error_is_explained(monkeypatch, tmp_path) -> None:
    install(monkeypatch, returncode=1)

    with pytest.raises(fixer.FixRefused, match="Codex stopped with an error"):
        fixer.run_codex(FAILURE, tmp_path, threading.Event())


def test_missing_answer_is_explained(monkeypatch, tmp_path) -> None:
    _, _, cancel = install(monkeypatch)

    with pytest.raises(fixer.FixRefused, match="answer was unreadable"):
        fixer.run_codex(FAILURE, tmp_path, cancel)


def test_codex_ready_explains_missing_or_logged_out(monkeypatch) -> None:
    monkeypatch.setattr(fixer.shutil, "which", lambda name: None)
    assert "isn't installed" in fixer.codex_ready()

    monkeypatch.setattr(fixer.shutil, "which", lambda name: "/opt/homebrew/bin/codex")
    monkeypatch.setattr(
        fixer.subprocess, "run", lambda *a, **k: fixer.subprocess.CompletedProcess(a, 1, "", "")
    )
    assert "isn't logged in" in fixer.codex_ready()

    monkeypatch.setattr(
        fixer.subprocess, "run", lambda *a, **k: fixer.subprocess.CompletedProcess(a, 0, "Logged in", "")
    )
    assert fixer.codex_ready() is None


def test_codex_gets_a_uv_cache_it_may_write(monkeypatch, tmp_path) -> None:
    # Found while recording the demo: inside the sandbox uv couldn't open ~/.cache/uv,
    # so Codex could never re-run a uv test to check its own fix.
    made, _, cancel = install(monkeypatch, answer={"fixed": True, "summary": "Fixed.", "verified": True})

    fixer.run_codex(FAILURE, tmp_path, cancel)

    env = made[0].env
    assert env["UV_CACHE_DIR"].startswith(fixer.tempfile.gettempdir())
    assert env["UV_NO_SYNC"] == "1"
    assert env["PATH"] == fixer.os.environ["PATH"]  # everything else is passed through
