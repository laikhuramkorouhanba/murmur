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
        "Murmur startup warning: OPENAI_API_KEY is missing; "
        "Murmur will use the macOS voice."
    ]


def test_present_key_has_no_warning_and_is_never_in_warning_text() -> None:
    secret = "test-key-that-must-not-be-printed"

    assert config.startup_warnings(secret) == []
    assert all(secret not in warning for warning in config.startup_warnings(secret))
