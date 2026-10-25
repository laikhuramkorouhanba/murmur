import pytest

from murmur import app, config, fixer, settings, voice, watcher


class SilentStream:
    """Stands in for the speakers. Tests that check audio swap in their own fake."""

    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass

    def write(self, _chunk):
        pass

    def stop(self):
        pass

    def abort(self):
        pass

    def close(self):
        pass


@pytest.fixture(autouse=True)
def private_settings_file(tmp_path, monkeypatch):
    """Give every test its own empty settings file, never the real one in your home folder."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    # An empty audio folder, so no test ever plays a real pre-rendered recording.
    monkeypatch.setattr(config, "AUDIO_DIR", tmp_path / "audio")
    # Fix it's saved diff and Codex log go in the test's own folder too.
    monkeypatch.setattr(fixer, "DIFF_PATH", tmp_path / "last_fix.diff")
    monkeypatch.setattr(fixer, "LOG_PATH", tmp_path / "last_fix_codex.log")
    # Whether VoiceOver happens to be on while tests run must never change what they check.
    monkeypatch.setattr(voice, "voiceover_running", lambda: False)
    # No test plays real sound (the pass and fail sounds come before almost everything now).
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", SilentStream)
    # Every test opens its own speaker stream, never one kept open by an earlier test.
    voice._drop_output()


@pytest.fixture(autouse=True)
def no_real_terminal_lookups(monkeypatch):
    """Tests never ask the real Terminal or ps/lsof which tab or folder a failure came from."""
    monkeypatch.setattr(app, "_lookup_front_tty", lambda: None)
    monkeypatch.setattr(app, "_lookup_folder", lambda tty: None)
    # The quick-command check must never stat a real tty or read a real tab's screen in tests.
    monkeypatch.setattr(watcher, "tty_output_time", lambda tty: None)

    def no_screen(tty):
        raise RuntimeError("tests never read a real Terminal screen")

    monkeypatch.setattr(watcher, "get_tab_contents", no_screen)
    fixer.forget_failure()
    fixer.remember_fix(None)
    yield
    fixer.forget_failure()
    fixer.remember_fix(None)
