import pytest

from murmur import settings


@pytest.fixture(autouse=True)
def private_settings_file(tmp_path, monkeypatch):
    """Give every test its own empty settings file, never the real one in your home folder."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
