import pytest

from murmur import settings


def test_defaults_when_nothing_is_saved() -> None:
    assert settings.get("auto_announce") is True
    assert settings.get("captions") is False
    assert settings.get("double_tap_key") == "right_command"
    assert settings.get("style") == "Brief"
    assert settings.get("engine") == "mac"
    assert settings.get("speed") == 0.5
    assert settings.get("auto_detail") == "short"
    assert settings.get("voice") == "marin"
    assert settings.get("welcome_done") is False


def test_a_saved_choice_is_remembered_and_others_are_kept() -> None:
    settings.set("voice", "cedar")
    settings.set("double_tap_key", "right_shift")

    assert settings.get("voice") == "cedar"
    assert settings.get("double_tap_key") == "right_shift"
    assert '"voice": "cedar"' in settings.PATH.read_text(encoding="utf-8")


def test_a_broken_file_falls_back_to_defaults() -> None:
    settings.PATH.write_text("{ not json", encoding="utf-8")

    assert settings.get("voice") == "marin"


def test_unknown_settings_are_refused() -> None:
    with pytest.raises(KeyError):
        settings.set("colour", "blue")


def test_old_settings_are_migrated() -> None:
    settings.PATH.write_text(
        '{"engine": "voiceover", "voice": "cedar", "speed": "very_fast", "style": "Friendly",'
        ' "auto_detail": "everything", "double_tap_key": "right_shift", "personality": "Smooth", "captions": true}',
        encoding="utf-8",
    )

    settings.migrate()

    assert settings.get("engine") == "mac"
    assert settings.get("voice") == "marin"
    assert settings.get("speed") == 0.75
    assert settings.get("style") == "Brief"
    assert settings.get("auto_detail") == "short"
    assert settings.get("double_tap_key") == "right_command"
    assert settings.get("captions") is True  # untouched
    assert "personality" not in settings.PATH.read_text(encoding="utf-8")

