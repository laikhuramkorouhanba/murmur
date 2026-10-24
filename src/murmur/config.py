"""Small set of settings shared by Murmur's M2 features."""

import os

from dotenv import load_dotenv


load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip() or None

SUMMARY_MODEL = "gpt-4o-mini"
SUMMARY_TEMPERATURE = 0
SUMMARY_MAX_TOKENS = 120
SUMMARY_TIMEOUT_SECONDS = 8
SUMMARY_TRIGGER_CHARS = 600
SUMMARY_MAX_INPUT_CHARS = 12000

TTS_MODEL = "gpt-4o-mini-tts"
VOICE = "cedar"
SPEECH_SPEED = 1.15
TTS_TIMEOUT_SECONDS = 8
FIRST_AUDIO_TIMEOUT_SECONDS = 5
SYSTEM_VOICE_RATE = 230

HOTKEY = "<cmd>+<alt>+<shift>+m"


def startup_warnings(api_key: str | None = OPENAI_API_KEY) -> list[str]:
    """Return plain warnings without ever including the API key itself."""
    if not api_key:
        return [
            "Murmur startup warning: OPENAI_API_KEY is missing; "
            "Murmur will use the macOS voice."
        ]
    return []
