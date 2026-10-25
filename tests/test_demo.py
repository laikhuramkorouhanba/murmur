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


# --- Demo mode ---------------------------------------------------------------------


@pytest.fixture
def demo_project(tmp_path, monkeypatch) -> Path:
    """A real demo project, built by the real builder, in a test folder."""
    assert build(tmp_path).returncode == 0
    project = tmp_path / "murmur-fixme-demo"
    monkeypatch.setattr(demo, "DEMO_DIR", project)
    monkeypatch.setattr(demo, "FIX_SECONDS", 0.1)
    monkeypatch.setattr(demo, "PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(demo, "reset_demo_project", lambda: (build(tmp_path), demo.RESET_LINE)[1])
    return project


@pytest.fixture
def heard(monkeypatch) -> list:
    said = []
    monkeypatch.setattr(app.voice, "speak", lambda text, **kwargs: said.append(text) or "pre-rendered")
    monkeypatch.setattr(app.voice, "play_soft_sound", lambda *a: "sound")
    return said


def test_demo_mode_replays_the_recording_through_the_real_pipeline(demo_project, heard) -> None:
    import threading

    from murmur import captions, settings

    demo.run_demo_mode(threading.Event(), announce=app.announce_last_output)

    assert heard[0] == "Demo mode. This is a replay of a recorded run."
    assert "it expected 21.60 but got 18.40" in heard[1]
    assert heard[2] in {*personality_working_cues()}
    assert "Undo last fix is in the Murmur menu." in heard[3]
    assert "subtotal + subtotal * tax_rate" in (demo_project / "shop.py").read_text()
    assert fixer.last_fix() is not None and fixer.last_fix().files == ["shop.py"]
    assert settings.get("captions") is True  # the room can read along


def personality_working_cues() -> set[str]:
    from murmur import personality

    smooth, focused = personality._FIX_LINES["working"]
    return {*smooth, focused}


def test_undo_after_demo_mode_puts_the_bug_back(demo_project, heard) -> None:
    import threading

    demo.run_demo_mode(threading.Event(), announce=app.announce_last_output)

    assert fixer.undo_last_fix(say=heard.append)
    assert demo.demo_project_ready()


def test_demo_mode_resets_a_project_that_was_already_fixed(demo_project, heard) -> None:
    import threading

    (demo_project / "shop.py").write_text("already fixed\n")
    assert not demo.demo_project_ready()

    demo.run_demo_mode(threading.Event(), announce=app.announce_last_output)

    assert "subtotal + subtotal * tax_rate" in (demo_project / "shop.py").read_text()


def test_stopping_demo_mode_early_changes_nothing(demo_project, heard) -> None:
    import threading

    cancel = threading.Event()
    cancel.set()

    demo.run_demo_mode(cancel, announce=app.announce_last_output)

    assert heard == ["Demo mode. This is a replay of a recorded run."]
    assert demo.demo_project_ready()


def test_the_recording_is_a_real_failure_and_a_one_file_fix() -> None:
    recording = demo.load_recording()

    assert "AssertionError: assert Decimal('18.40') == Decimal('21.60')" in recording["output"]
    assert recording["files"] == ["shop.py"]
    assert "+    total = subtotal + subtotal * tax_rate" in (demo.RECORDED / "fix.diff").read_text()


def test_demo_mode_menu_item_has_a_spoken_label() -> None:
    label = str(app.MurmurApp().demo_item._menuitem.accessibilityLabel())

    assert label.startswith("Demo mode: replay a recorded failure and fix")
