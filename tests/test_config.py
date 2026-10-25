import pytest

from murmur import config


def test_model_voice_speed_hotkey_and_limits_are_centralized() -> None:
    assert config.SUMMARY_MODEL == "gpt-4o-mini"
    assert config.SUMMARY_TEMPERATURE == 0
    assert config.SUMMARY_MAX_TOKENS == 120
    assert config.SUMMARY_TIMEOUT_SECONDS == 8
    assert config.TTS_MODEL == "gpt-4o-mini-tts"
    assert config.VOICE == "marin"
    assert config.SPEECH_SPEED == 1.0
    assert config.HOTKEY_KEY_CODE == 46
    assert config.DOUBLE_TAP_SECONDS == 0.4
    assert config.HOTKEY_LABEL == "⌘⌥⇧M"
    assert config.SYSTEM_VOICE_RATE == 180


def test_missing_key_warning_says_speech_will_use_macos_voice() -> None:
    assert config.startup_warnings("") == [
        "Murmur startup warning: no OpenAI API key found. Murmur will ask for one "
        "(or use Set OpenAI API key… in the menu) and uses the macOS voice until then."
    ]


def test_present_key_has_no_warning_and_is_never_in_warning_text() -> None:
    secret = "test-key-that-must-not-be-printed"

    assert config.startup_warnings(secret) == []
    assert all(secret not in warning for warning in config.startup_warnings(secret))


def test_the_keychain_key_comes_first(monkeypatch) -> None:
    monkeypatch.setattr(config.keychain, "read_key", lambda: "sk-from-keychain")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    assert config.load_api_key() == ("sk-from-keychain", "the Keychain")


def test_dot_env_is_the_fallback_while_developing(monkeypatch) -> None:
    monkeypatch.setattr(config.keychain, "read_key", lambda: None)
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    assert config.load_api_key() == ("sk-from-env", ".env")


def test_the_built_app_never_reads_dot_env(monkeypatch) -> None:
    monkeypatch.setattr(config.keychain, "read_key", lambda: None)
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config, "load_dotenv", lambda: pytest.fail("the .app must not read .env"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    assert config.load_api_key() == (None, None)


def test_a_new_key_is_used_straight_away(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    monkeypatch.setattr(config, "API_KEY_SOURCE", None)

    config.use_api_key("sk-new", "the Keychain")

    assert config.OPENAI_API_KEY == "sk-new" and config.API_KEY_SOURCE == "the Keychain"
