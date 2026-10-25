"""Small set of settings shared by Murmur's M2 features."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from murmur import keychain


def is_packaged() -> bool:
    """True inside the built Murmur.app (PyInstaller sets sys.frozen), False with `uv run`."""
    return bool(getattr(sys, "frozen", False))


def load_api_key() -> tuple[str | None, str | None]:
    """Find the OpenAI key: the Keychain first, then .env (only when developing). Returns (key, where)."""
    key = keychain.read_key()
    if key:
        return key, "the Keychain"
    if not is_packaged():
        # Developing with `uv run`: a .env file (or an exported variable) is fine.
        load_dotenv()
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if key:
            return key, ".env"
    return None, None


OPENAI_API_KEY, API_KEY_SOURCE = load_api_key()

# Where pre-rendered audio lives. The same path works with `uv run` and inside Murmur.app.
AUDIO_DIR = Path(__file__).parent / "audio"


def use_api_key(key: str, source: str) -> None:
    """Switch to a new key straight away, without restarting Murmur."""
    global OPENAI_API_KEY, API_KEY_SOURCE
    OPENAI_API_KEY, API_KEY_SOURCE = key, source

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

# Who does the talking. Picked in the "Voice" menu; "auto" means VoiceOver when
# it's running, and the Mac voice when it isn't.
ENGINES = {"voiceover": "VoiceOver", "mac": "Mac voice", "natural": "Natural voice"}

# The "Speed" menu. Screen reader users often listen at two to three times normal speed.
SPEEDS = {"normal": "Normal", "fast": "Fast", "very_fast": "Very fast", "maximum": "Maximum"}
# OpenAI's speed (0.25 to 4). Measured on one sentence: 1.0 took 12.0 s, 1.5 took
# 7.5 s, 2.0 took 5.3 s, 3.0 took 4.5 s. Above 3 it gains little and gets harder to follow.
NATURAL_SPEEDS = {"normal": 1.0, "fast": 1.5, "very_fast": 2.0, "maximum": 3.0}
# The Mac voice's rate in words per minute (say -r). Measured: it stops getting
# faster at about 400, so asking for more than 450 changes nothing.
MAC_VOICE_RATES = {"normal": 200, "fast": 300, "very_fast": 380, "maximum": 450}
# VoiceOver never tells us when it has finished a line, so we wait about this long
# per character before the next one, to avoid talking over it.
VOICEOVER_SECONDS_PER_CHAR = 1 / 30
TTS_CONNECT_TIMEOUT_SECONDS = 2.0  # if Wi-Fi is dead, give up quickly and use the Mac voice
# How long we wait for OpenAI to send audio. We measured 0.7 to 2.6 s, and on
# stage more than about 4 s of silence sounds broken, while `say` starts in 0.3 s.
FIRST_AUDIO_TIMEOUT_SECONDS = 3.5
# OpenAI sometimes takes many seconds to start one request. If nothing has
# arrived after this long, a second identical request is sent in parallel.
TTS_HEDGE_SECONDS = 1.5
TTS_KEEPALIVE_SECONDS = 120  # keep the connection to OpenAI open this long between lines

# The M key's physical key code on a Mac keyboard. We match this, not the letter,
# because holding Option changes which letter macOS reports.
HOTKEY_KEY_CODE = 46
HOTKEY_LABEL = "⌘⌥⇧M"
HOTKEY_SPOKEN = "Command Option Shift M"
FIX_KEY_CODE = 3  # the F key's physical code, so Option can't turn it into another letter
FIX_HOTKEY_LABEL = "⌘⌥⇧F"
FIX_HOTKEY_SPOKEN = "Command Option Shift F"
READ_KEY_CODE = 15  # the R key's physical code
READ_HOTKEY_LABEL = "⌘⌥⇧R"
READ_HOTKEY_SPOKEN = "Command Option Shift R"

# Keys you can double-tap to start Murmur. Pick one in the "Double-tap key" menu.
DOUBLE_TAP_KEYS = {
    "right_command": "Right Command",
    "right_option": "Right Option",
    "right_shift": "Right Shift",
}
DOUBLE_TAP_SECONDS = 0.4

# The Murmur key layer: double tap, Murmur says "Murmur", then one of these keys
# (matched by physical key code). The key is swallowed, so Terminal never sees it.
KEY_LAYER_SECONDS = 3.0  # with no key by then, the layer closes by itself
KEY_LAYER_KEYS = {
    49: "repeat",  # Space: say the last announcement again
    15: "read",  # R: read the full output word for word
    3: "fix",  # F: fix the last error with Codex
    32: "undo",  # U: undo the last fix
    53: "cancel",  # Escape: close the layer and stop speech
}

# Fix it (Codex). Low reasoning effort keeps fixes quick on stage.
FIX_TIMEOUT_SECONDS = 180
FIX_REASONING_EFFORT = "low"  # Codex accepts none, minimal, low, medium, high, xhigh, max
FIX_TICK_SECONDS = 10  # a quiet tick this often while Codex works
FIX_STILL_ON_IT_SECONDS = 30  # after this long, also say one short "still on it" line
FIX_MAX_ERROR_CHARS = 12000

# Auto-announce: how often we peek at Terminal, and how we treat finished commands.
AUTO_POLL_SECONDS = 0.25  # the free tty check runs this often
AUTO_SNAPSHOT_SECONDS = 1.0  # asking Terminal "busy?" costs about 0.2 s, so only once a second
AUTO_QUIET_SECONDS = 0.5  # after output stops for this long, look at the screen once
AUTO_SAME_FINISH_SECONDS = 3.0  # both checks seeing one command within this time count once
AUTO_ERROR_POLL_SECONDS = 5.0  # slower while something is wrong, so errors don't spam
AUTO_SHORT_SECONDS = 2.0  # with "Errors only", a success quicker than this stays silent
AUTO_LONG_SUCCESS_SECONDS = 15.0  # a command with no output that ran this long says "Done."
# The "Auto-announce" menu. Short output is read word for word; it's quicker than a summary.
AUTO_DETAILS = {"errors": "Errors only", "short": "Errors and short output", "everything": "Everything"}
SHORT_OUTPUT_LINES = 10
SHORT_OUTPUT_CHARS = 800  # ten very long lines are not "short"
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
            "Murmur startup warning: no OpenAI API key found. Murmur will ask for one "
            "(or use Set OpenAI API key… in the menu) and uses the macOS voice until then."
        ]
    return []
