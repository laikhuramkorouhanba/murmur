"""The demo project builder's safety marker, and Reset demo project."""

import os
import subprocess
from pathlib import Path

import pytest

from murmur import app, demo, fixer

BUILDER = demo.BUILDER


def build(home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run([str(BUILDER)], capture_output=True, text=True, env=env, check=False)


def test_builder_makes_the_demo_with_its_marker_and_its_bug(tmp_path) -> None:
    assert build(tmp_path).returncode == 0

    project = tmp_path / "murmur-fixme-demo"
    assert (project / ".murmur-demo").exists()
    assert "subtotal - subtotal * tax_rate" in (project / "shop.py").read_text()
    assert not (project / "__pycache__").exists()


def test_builder_rebuilds_its_own_demo(tmp_path) -> None:
    build(tmp_path)
    (tmp_path / "murmur-fixme-demo" / "shop.py").write_text("fixed\n")

    assert build(tmp_path).returncode == 0
    assert "subtotal - subtotal" in (tmp_path / "murmur-fixme-demo" / "shop.py").read_text()


def test_builder_never_deletes_a_folder_it_did_not_make(tmp_path) -> None:
    precious = tmp_path / "murmur-fixme-demo"
    precious.mkdir()
    (precious / "notes.txt").write_text("mine\n")

    result = build(tmp_path)

    assert result.returncode == 2
    assert "not made by Murmur" in result.stderr
    assert (precious / "notes.txt").read_text() == "mine\n"


def test_reset_forgets_the_old_failure_and_fix(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(demo, "BUILDER", Path("/usr/bin/true"))
    fixer.remember_failure(fixer.LastFailure(None, str(tmp_path), "pytest", "boom"))
    fixer.remember_fix(fixer.LastFix(tmp_path, "a", "b", ["shop.py"]))
    fixer.DIFF_PATH.write_text("old diff\n")

    assert demo.reset_demo_project() == "Demo project reset. The bug is back."

    assert fixer.last_failure() is None and fixer.last_fix() is None
    assert not fixer.DIFF_PATH.exists()


def test_a_refused_reset_is_explained(monkeypatch, tmp_path) -> None:
    script = tmp_path / "refuse.sh"
    script.write_text("#!/bin/sh\necho 'Refusing to delete it: not made by Murmur' >&2\nexit 2\n")
    script.chmod(0o755)
    monkeypatch.setattr(demo, "BUILDER", script)

    with pytest.raises(demo.DemoProblem, match="wasn't reset: Refusing to delete it"):
        demo.reset_demo_project()


def test_reset_menu_item_has_a_spoken_label() -> None:
    item = app.MurmurApp().reset_demo_item

    assert str(item._menuitem.accessibilityLabel()).startswith("Reset demo project:")
