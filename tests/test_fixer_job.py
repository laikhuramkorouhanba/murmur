"""The Fix job, start to finish, with a pretend Codex that edits a real temporary git repo."""

import subprocess
import threading
from pathlib import Path

import pytest

from murmur import config, fixer, personality, settings


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "shop.py").write_text("def total(price, tip):\n    return price - tip\n")
    git(root, "add", "shop.py")
    git(root, "commit", "-qm", "start")
    return root


@pytest.fixture(autouse=True)
def focused_mode() -> None:
    # Focused always picks the same words, so the tests can check them exactly.
    settings.set("personality", "Focused")


@pytest.fixture
def failure(repo: Path) -> fixer.LastFailure:
    failed = fixer.LastFailure(tty="/dev/ttys001", folder=str(repo), command="pytest -q", output="assert 8 == 12")
    fixer.remember_failure(failed)
    return failed


class Recorder:
    """Collects everything Murmur would have said or ticked."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.ticks = 0

    def say(self, text: str) -> None:
        self.said.append(text)

    def tick(self) -> None:
        self.ticks += 1


def run(recorder: Recorder, runner, *, cancel: threading.Event | None = None, ready=lambda: None):
    return fixer.fix_last_failure(
        cancel or threading.Event(), say=recorder.say, tick=recorder.tick, runner=runner, ready=ready
    )


def good_codex(failure, root, cancel, on_tick=None, log_file=None):
    on_tick(0.0)
    (root / "shop.py").write_text("def total(price, tip):\n    return price + tip\n")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "shop.cpython-313.pyc").write_bytes(b"\x00junk")
    return fixer.CodexAnswer(fixed=True, summary="Changed total to add the tip.", verified=True)


def test_a_good_fix_is_spoken_saved_and_remembered(repo: Path, failure, capsys) -> None:
    recorder = Recorder()

    fix = run(recorder, good_codex)

    assert recorder.said == [
        "Working on a fix.",
        "Fix applied. Changed shop.py. Changed total to add the tip. "
        "The command passes now. Undo last fix is in the Murmur menu.",
    ]
    assert fix is not None and fix.files == ["shop.py"]  # the cache file is left out
    assert fixer.last_fix() == fix
    assert fixer.last_failure() is None  # fixed, so nothing left to fix
    assert "+    return price + tip" in fixer.DIFF_PATH.read_text()
    assert ".pyc" not in fixer.DIFF_PATH.read_text()
    printed = capsys.readouterr().out
    assert "Fix took" in printed and "+    return price + tip" in printed


def test_tests_passing_is_only_claimed_when_codex_checked(repo: Path, failure) -> None:
    def unchecked_codex(failure, root, cancel, on_tick=None, log_file=None):
        (root / "shop.py").write_text("fixed\n")
        return fixer.CodexAnswer(fixed=True, summary="Fixed it.", verified=False)

    recorder = Recorder()
    run(recorder, unchecked_codex)

    assert "passes now" not in recorder.said[-1]


def test_undo_after_a_fix_also_cleans_up_the_cache_files(repo: Path, failure) -> None:
    fix = run(Recorder(), good_codex)

    fixer.undo(fix.root, fix.before, fix.after)

    assert "price - tip" in (repo / "shop.py").read_text()
    assert not (repo / "__pycache__" / "shop.cpython-313.pyc").exists()


def test_nothing_to_fix_when_the_last_command_worked(repo: Path) -> None:
    recorder = Recorder()

    assert run(recorder, good_codex) is None
    assert recorder.said == ["Nothing to fix. The last command didn't fail."]


def test_says_why_when_codex_is_not_ready(repo: Path, failure) -> None:
    recorder = Recorder()

    run(recorder, good_codex, ready=lambda: "Codex isn't logged in. Run: codex login")

    assert recorder.said == ["Codex isn't logged in. Run: codex login"]
    assert "price - tip" in (repo / "shop.py").read_text()


def test_refuses_outside_a_git_project(tmp_path: Path) -> None:
    fixer.remember_failure(fixer.LastFailure(None, str(tmp_path), "pytest", "boom"))
    recorder = Recorder()

    run(recorder, good_codex)

    assert recorder.said == ["No fix made. Fix it only works inside a git project, so every change can be undone."]


def test_a_codex_error_rolls_back_half_made_changes(repo: Path, failure) -> None:
    def broken_codex(failure, root, cancel, on_tick=None, log_file=None):
        (root / "shop.py").write_text("half done\n")
        (root / "new_file.py").write_text("oops\n")
        raise fixer.FixRefused("Codex ran out of time.")

    recorder = Recorder()
    assert run(recorder, broken_codex) is None

    assert recorder.said == ["No fix made. Codex ran out of time. Nothing was changed."]
    assert "price - tip" in (repo / "shop.py").read_text()
    assert not (repo / "new_file.py").exists()
    assert fixer.last_fix() is None


def test_cancelling_rolls_back_and_stays_quiet(repo: Path, failure) -> None:
    cancel = threading.Event()

    def cancelled_codex(failure, root, cancel_event, on_tick=None, log_file=None):
        (root / "shop.py").write_text("half done\n")
        cancel_event.set()
        raise fixer.FixRefused("Fix cancelled.")

    recorder = Recorder()
    run(recorder, cancelled_codex, cancel=cancel)

    assert recorder.said == []
    assert "price - tip" in (repo / "shop.py").read_text()


def test_codex_finding_no_safe_fix_leaves_the_project_clean(repo: Path, failure) -> None:
    def unsure_codex(failure, root, cancel, on_tick=None, log_file=None):
        (root / "shop.py").write_text("a guess\n")
        return fixer.CodexAnswer(fixed=False, summary="The test expects a value I can't work out.", verified=False)

    recorder = Recorder()
    run(recorder, unsure_codex)

    assert recorder.said == ["No fix made. The test expects a value I can't work out. Nothing was changed."]
    assert "price - tip" in (repo / "shop.py").read_text()


def test_a_fix_that_only_touched_cache_files_counts_as_no_fix(repo: Path, failure) -> None:
    def cache_only_codex(failure, root, cancel, on_tick=None, log_file=None):
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        return fixer.CodexAnswer(fixed=True, summary="", verified=False)

    recorder = Recorder()
    run(recorder, cache_only_codex)

    assert recorder.said == ["No fix made. Codex didn't change any files. Nothing was changed."]


def test_progress_cue_ticks_and_one_still_on_it_line(monkeypatch) -> None:
    monkeypatch.setattr(config, "FIX_TICK_SECONDS", 10)
    monkeypatch.setattr(config, "FIX_STILL_ON_IT_SECONDS", 30)
    recorder = Recorder()
    progress = fixer._Progress(threading.Event(), recorder.say, recorder.tick)

    for second in [0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60]:
        progress(float(second))

    assert recorder.said == ["Working on a fix.", "Still working."]
    assert recorder.ticks == 5  # at 10, 20, 40, 50 and 60 (30 is the "still on it" line instead)


def test_progress_stays_quiet_once_cancelled() -> None:
    cancel = threading.Event()
    cancel.set()
    recorder = Recorder()

    fixer._Progress(cancel, recorder.say, recorder.tick)(0.0)

    assert recorder.said == [] and recorder.ticks == 0


@pytest.mark.parametrize(
    "path, junk",
    [
        ("__pycache__/shop.cpython-313.pyc", True),
        ("src/pkg/__pycache__/a.cpython-311.pyc", True),
        (".pytest_cache/v/cache/lastfailed", True),
        ("old.pyc", True),
        (".DS_Store", True),
        ("shop.py", False),
        ("src/pycache_helpers.py", False),
        ("node_modules_notes.md", False),
    ],
)
def test_cache_files_are_spotted(path: str, junk: bool) -> None:
    assert fixer.is_junk(path) is junk


def test_file_list_reads_naturally() -> None:
    assert fixer._file_list(["src/shop.py"]) == "shop.py"
    assert fixer._file_list(["a.py", "b.py"]) == "a.py and b.py"
    assert fixer._file_list(["a.py", "b.py", "c.py", "d.py", "e.py"]) == "a.py, b.py and c.py and 2 more"


def test_smooth_fix_lines_never_repeat_twice_in_a_row() -> None:
    settings.set("personality", "Smooth")
    lines = [personality.fix_line("fixed") for _ in range(20)]

    assert all(first != second for first, second in zip(lines, lines[1:]))
    assert "Patched it, cool cat." in lines
