import pytest

from murmur import settings


def test_defaults_when_nothing_is_saved() -> None:
    assert settings.get("auto_announce") is True
    assert settings.get("captions") is False
    assert settings.get("double_tap_key") == "right_command"
    assert settings.get("personality") == "Smooth"
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
