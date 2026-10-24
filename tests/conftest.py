import pytest

from murmur import app, fixer, settings


@pytest.fixture(autouse=True)
def private_settings_file(tmp_path, monkeypatch):
    """Give every test its own empty settings file, never the real one in your home folder."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")


@pytest.fixture(autouse=True)
def no_real_terminal_lookups(monkeypatch):
    """Tests never ask the real Terminal or ps/lsof which tab or folder a failure came from."""
    monkeypatch.setattr(app, "_lookup_front_tty", lambda: None)
    monkeypatch.setattr(app, "_lookup_folder", lambda tty: None)
    fixer.forget_failure()
    yield
    fixer.forget_failure()
