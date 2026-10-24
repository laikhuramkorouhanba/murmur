import subprocess
from pathlib import Path

import pytest

from murmur import fixer


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "checkout.py").write_text("total = (18\n")
    (root / "notes.txt").write_text("keep me\n")
    git(root, "add", "checkout.py", "notes.txt")
    git(root, "commit", "-qm", "start")
    (root / "scratch.py").write_text("untracked but mine\n")  # never committed
    return root


def test_project_root_is_the_git_top_folder(repo: Path) -> None:
    (repo / "src").mkdir()

    assert fixer.project_root(str(repo / "src")) == repo.resolve()


def test_refuses_a_folder_that_is_not_a_git_project(tmp_path: Path) -> None:
    with pytest.raises(fixer.FixRefused, match="only works inside a git project"):
        fixer.project_root(str(tmp_path))


def test_refuses_when_the_git_project_is_the_home_folder(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(fixer.Path, "home", lambda: repo)

    with pytest.raises(fixer.FixRefused, match="whole home folder"):
        fixer.project_root(str(repo))


def test_refuses_an_unknown_folder() -> None:
    with pytest.raises(fixer.FixRefused, match="don't know which folder"):
        fixer.project_root(None)


def test_snapshots_never_touch_your_real_git_state(repo: Path) -> None:
    (repo / "notes.txt").write_text("staged edit\n")
    git(repo, "add", "notes.txt")
    status_before = git(repo, "status", "--porcelain")
    staged_before = git(repo, "diff", "--cached")

    fixer.snapshot(repo)

    assert git(repo, "status", "--porcelain") == status_before
    assert git(repo, "diff", "--cached") == staged_before
    assert git(repo, "log", "--oneline").count("\n") == 1  # no new commits


def test_the_diff_shows_exactly_what_changed_including_new_and_untracked_files(repo: Path) -> None:
    before = fixer.snapshot(repo)
    (repo / "checkout.py").write_text("total = (18)\n")
    (repo / "helpers.py").write_text("def tip(): ...\n")
    (repo / "scratch.py").write_text("edited by codex\n")
    after = fixer.snapshot(repo)

    assert sorted(fixer.changed_files(repo, before, after)) == [
        ("A", "helpers.py"),
        ("M", "checkout.py"),
        ("M", "scratch.py"),
    ]
    diff = fixer.diff_text(repo, before, after)
    assert "-total = (18\n+total = (18)" in diff


def test_undo_puts_everything_back(repo: Path) -> None:
    before = fixer.snapshot(repo)
    (repo / "checkout.py").write_text("total = (18)\n")
    (repo / "helpers.py").write_text("def tip(): ...\n")
    (repo / "scratch.py").write_text("edited by codex\n")
    (repo / "notes.txt").unlink()
    after = fixer.snapshot(repo)

    restored = fixer.undo(repo, before, after)

    assert sorted(restored) == ["checkout.py", "helpers.py", "notes.txt", "scratch.py"]
    assert (repo / "checkout.py").read_text() == "total = (18\n"
    assert (repo / "scratch.py").read_text() == "untracked but mine\n"
    assert (repo / "notes.txt").read_text() == "keep me\n"
    assert not (repo / "helpers.py").exists()
    assert fixer.snapshot(repo) == before


def test_undo_refuses_when_you_edited_a_fixed_file_afterwards(repo: Path) -> None:
    before = fixer.snapshot(repo)
    (repo / "checkout.py").write_text("total = (18)\n")
    after = fixer.snapshot(repo)
    (repo / "checkout.py").write_text("total = (18)  # my own tweak\n")

    with pytest.raises(fixer.FixRefused, match="You changed checkout.py after the fix"):
        fixer.undo(repo, before, after)

    assert (repo / "checkout.py").read_text() == "total = (18)  # my own tweak\n"


def test_undo_with_no_changes_does_nothing(repo: Path) -> None:
    before = fixer.snapshot(repo)

    assert fixer.undo(repo, before, before) == []


def test_works_in_a_brand_new_repo_with_no_commits(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    root.mkdir()
    git(root, "init", "-q")
    (root / "a.py").write_text("x = 1\n")

    before = fixer.snapshot(root)
    (root / "a.py").write_text("x = 2\n")

    assert fixer.changed_files(root, before, fixer.snapshot(root)) == [("M", "a.py")]
