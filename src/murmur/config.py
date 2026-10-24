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
VOICE = "marin"
# OpenAI voices you can pick in the "Voice" menu.
VOICES = {"marin": "Marin", "cedar": "Cedar"}
SPEECH_SPEED = 1.0
TTS_TIMEOUT_SECONDS = 8
FIRST_AUDIO_TIMEOUT_SECONDS = 5
SYSTEM_VOICE_RATE = 180

# The M key's physical key code on a Mac keyboard. We match this, not the letter,
# because holding Option changes which letter macOS reports.
HOTKEY_KEY_CODE = 46
HOTKEY_LABEL = "⌘⌥⇧M"
HOTKEY_SPOKEN = "Command Option Shift M"

# Keys you can double-tap to start Murmur. Pick one in the "Double-tap key" menu.
DOUBLE_TAP_KEYS = {
    "right_command": "Right Command",
    "right_option": "Right Option",
    "right_shift": "Right Shift",
}
DOUBLE_TAP_SECONDS = 0.4

# Auto-announce: how often we peek at Terminal, and how we treat finished commands.
AUTO_POLL_SECONDS = 1.0
AUTO_ERROR_POLL_SECONDS = 5.0  # slower while something is wrong, so errors don't spam
AUTO_SHORT_SECONDS = 2.0  # quicker than this and successful: stay silent
AUTO_LONG_SUCCESS_SECONDS = 15.0  # this long and successful: say one short line
# Full-screen or never-ending programs. Leaving them is not a "finished command".
AUTO_IGNORED_PROGRAMS = frozenset(
    {
        "vim", "nvim", "vi", "nano", "emacs", "less", "more", "man",
        "top", "htop", "btop", "ssh", "mosh", "tmux", "screen", "watch",
    }
)


def startup_warnings(api_key: str | None = OPENAI_API_KEY) -> list[str]:
    """Return plain warnings without ever including the API key itself."""
    if not api_key:
        return [
            "Murmur startup warning: OPENAI_API_KEY is missing; "
            "Murmur will use the macOS voice."
        ]
    return []
