"""Speaking styles: Brief (the default) and Friendly.

Brief is for screen reader users, who often listen at two or three times normal
speed: the status word first ("Failed."), then the file, line and exact cause.
Friendly adds a warm opener and closer, like the old Smooth mode.
"""

from enum import Enum
import random
import re
import threading

from murmur import config, settings


class Mode(str, Enum):
    BRIEF = "Brief"
    FRIENDLY = "Friendly"


_LOCK = threading.Lock()
_FAILURE_STREAK = 0
_LAST_OPENER: str | None = None
_LAST_CLOSER: str | None = None
_LAST_DONE_LINE: str | None = None
_RANDOM = random.Random()

_PLAYFUL_OPENERS = (
    "Ooh, a small hiccup, cool cat.",
    "Mmm, one little snag.",
    "Alright, a little bump in the road.",
    "Ooh, one note out of tune.",
)
_ENCOURAGING_OPENERS = (
    "We'll take it one step at a time.",
    "You've got this; let's work through it.",
    "Let's take a breath and handle one detail at a time.",
)
_SUCCESS_OPENERS = (
    "Mmm, nice and tidy.",
    "Alright, looking good.",
    "Ooh, smooth sailing.",
)
_PLAYFUL_CLOSERS = (
    "Nothing we can't smooth out.",
    "Easy fix, I reckon.",
    "We'll have it humming again soon.",
    "Just a small detour.",
)
_ENCOURAGING_CLOSERS = (
    "Steady wins this one.",
    "Each run gets us closer.",
    "Take your time; I'm right here.",
)
_SUCCESS_CLOSERS = (
    "Carry on, cool cat.",
    "All yours.",
    "Onward.",
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def get_mode() -> Mode:
    """Always Brief now. Friendly stays in the code for later, but isn't offered in the menu."""
    try:
        return Mode(settings.get("style"))
    except (TypeError, ValueError):
        return Mode.BRIEF


def set_mode(mode: Mode | str) -> None:
    settings.set("style", Mode(mode).value)


def record_result(failed: bool) -> int:
    """Track consecutive failures so Friendly can stay encouraging after a run."""
    global _FAILURE_STREAK
    with _LOCK:
        _FAILURE_STREAK = _FAILURE_STREAK + 1 if failed else 0
        return _FAILURE_STREAK


def failure_streak() -> int:
    with _LOCK:
        return _FAILURE_STREAK


def _next_opener(options: tuple[str, ...]) -> str:
    """Pick a random opener, but never the one we used last time."""
    global _LAST_OPENER
    with _LOCK:
        choices = tuple(option for option in options if option != _LAST_OPENER) or options
        _LAST_OPENER = _RANDOM.choice(choices)
        return _LAST_OPENER


def _next_closer(options: tuple[str, ...]) -> str:
    """Same idea for the closing line: random, and never the same twice in a row."""
    global _LAST_CLOSER
    with _LOCK:
        choices = tuple(option for option in options if option != _LAST_CLOSER) or options
        _LAST_CLOSER = _RANDOM.choice(choices)
        return _LAST_CLOSER


def _with_closer(text: str, facts: str, options: tuple[str, ...]) -> str:
    # Summaries of up to two sentences get a closer; longer ones don't get even longer.
    if len(_SENTENCE_BOUNDARY.split(facts.strip())) > 2:
        return text
    return f"{text} {_next_closer(options)}"


def style_summary(facts: str, *, failed: bool) -> str:
    """Add a small Friendly flourish, or leave Brief wording untouched."""
    mode = get_mode()
    if mode is Mode.BRIEF:
        return facts

    if failed and failure_streak() >= 3:
        opener = _next_opener(_ENCOURAGING_OPENERS)
        return _with_closer(f"{opener} {facts}", facts, _ENCOURAGING_CLOSERS)

    if failed:
        opener = _next_opener(_PLAYFUL_OPENERS)
        return _with_closer(f"{opener} {facts}", facts, _PLAYFUL_CLOSERS)

    opener = _next_opener(_SUCCESS_OPENERS)
    return _with_closer(f"{opener} {facts}", facts, _SUCCESS_CLOSERS)


_FRIENDLY_DONE_LINES = (
    "All done, smooth as butter.",
    "All done, not a single snag.",
    "Finished, and clean as a whistle.",
)


def done_line() -> str:
    """One short line for a long command that finished without errors."""
    global _LAST_DONE_LINE
    if get_mode() is Mode.BRIEF:
        return "Done."
    with _LOCK:
        choices = tuple(line for line in _FRIENDLY_DONE_LINES if line != _LAST_DONE_LINE)
        _LAST_DONE_LINE = _RANDOM.choice(choices)
        return _LAST_DONE_LINE


# Lines for Fix it. Friendly picks a random one (never the same twice in a row);
# Brief always says the same short word or two.
_FIX_LINES = {
    "working": (
        ("On it, give me a moment.", "Leave it with me.", "Rolling up my sleeves."),
        "Fixing.",
    ),
    "still": (
        ("Still on it, hang tight.", "Still cooking, nearly there.", "Bear with me, still digging."),
        "Still fixing.",
    ),
    "fixed": (
        ("Patched it, cool cat.", "Fixed it up.", "All patched."),
        "Fixed.",
    ),
    "not_fixed": (
        ("Hmm, no fix this time.", "Ooh, that one beat me."),
        "Not fixed.",
    ),
}
_LAST_FIX_LINE: dict[str, str] = {}


def fix_line(kind: str) -> str:
    """One short Fix it line: "working", "still", "fixed" or "not_fixed"."""
    friendly_options, brief = _FIX_LINES[kind]
    if get_mode() is Mode.BRIEF:
        return brief
    with _LOCK:
        choices = tuple(line for line in friendly_options if line != _LAST_FIX_LINE.get(kind))
        _LAST_FIX_LINE[kind] = _RANDOM.choice(choices)
        return _LAST_FIX_LINE[kind]


def sample_sentence() -> str:
    return sample_sentence_for(get_mode())


def sample_sentence_for(mode: Mode | str) -> str:
    if Mode(mode) is Mode.FRIENDLY:
        return "Ooh, Murmur is ready to roll, cool cat."
    return "Murmur voice check."


def tts_instructions(mode: Mode | str | None = None) -> str:
    """Set a delivery style without asking the voice to change the text."""
    selected = Mode(mode) if mode is not None else get_mode()
    if selected is Mode.FRIENDLY:
        return (
            "Warm, relaxed and a little playful, like a friendly radio host, at a natural, lively pace. "
            "Keep every word clear. Say file names naturally and read line numbers clearly. "
            "Do not add words or change the provided text."
        )
    return (
        "Clear, neutral and brisk, like a screen reader. Keep every word easy to understand. "
        "Say file names naturally and read line numbers plainly. "
        "Do not add words or change the provided text."
    )
