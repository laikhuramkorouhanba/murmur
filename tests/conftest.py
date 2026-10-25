import pytest

from murmur import app, fixer, settings


@pytest.fixture(autouse=True)
def private_settings_file(tmp_path, monkeypatch):
    """Give every test its own empty settings file, never the real one in your home folder."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    # Fix it's saved diff and Codex log go in the test's own folder too.
    monkeypatch.setattr(fixer, "DIFF_PATH", tmp_path / "last_fix.diff")
    monkeypatch.setattr(fixer, "LOG_PATH", tmp_path / "last_fix_codex.log")


@pytest.fixture(autouse=True)
def no_real_terminal_lookups(monkeypatch):
    """Tests never ask the real Terminal or ps/lsof which tab or folder a failure came from."""
    monkeypatch.setattr(app, "_lookup_front_tty", lambda: None)
    monkeypatch.setattr(app, "_lookup_folder", lambda tty: None)
    fixer.forget_failure()
    fixer.remember_fix(None)
    yield
    fixer.forget_failure()
    fixer.remember_fix(None)
