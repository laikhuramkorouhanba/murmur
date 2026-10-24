"""Runtime personality modes for spoken summaries."""

from enum import Enum
import random
import re
import threading

from murmur import settings


class Mode(str, Enum):
    SMOOTH = "Smooth"
    FOCUSED = "Focused"


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
    try:
        return Mode(settings.get("personality"))
    except (TypeError, ValueError):
        return Mode.SMOOTH


def set_mode(mode: Mode | str) -> None:
    settings.set("personality", Mode(mode).value)


def record_result(failed: bool) -> int:
    """Track consecutive failures so Smooth can stay encouraging after a run."""
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
    # Only short summaries get a closer, so longer ones don't get even longer.
    if len(_SENTENCE_BOUNDARY.split(facts.strip())) > 1:
        return text
    return f"{text} {_next_closer(options)}"


def style_summary(facts: str, *, failed: bool) -> str:
    """Add a small Smooth flourish, or leave Focused wording untouched."""
    mode = get_mode()
    if mode is Mode.FOCUSED:
        return facts

    if failed and failure_streak() >= 3:
        opener = _next_opener(_ENCOURAGING_OPENERS)
        return _with_closer(f"{opener} {facts}", facts, _ENCOURAGING_CLOSERS)

    if failed:
        opener = _next_opener(_PLAYFUL_OPENERS)
        return _with_closer(f"{opener} {facts}", facts, _PLAYFUL_CLOSERS)

    opener = _next_opener(_SUCCESS_OPENERS)
    return _with_closer(f"{opener} {facts}", facts, _SUCCESS_CLOSERS)


_SMOOTH_DONE_LINES = (
    "All done, smooth as butter.",
    "All done, not a single snag.",
    "Finished, and clean as a whistle.",
)


def done_line() -> str:
    """One short line for a long command that finished without errors."""
    global _LAST_DONE_LINE
    if get_mode() is Mode.FOCUSED:
        return "Done, no errors."
    with _LOCK:
        choices = tuple(line for line in _SMOOTH_DONE_LINES if line != _LAST_DONE_LINE)
        _LAST_DONE_LINE = _RANDOM.choice(choices)
        return _LAST_DONE_LINE


def sample_sentence() -> str:
    if get_mode() is Mode.SMOOTH:
        return "Ooh, Murmur is ready to roll, cool cat."
    return "Murmur voice check is ready."


def tts_instructions(mode: Mode | str | None = None) -> str:
    """Set a delivery style without asking the voice to change the text."""
    selected = Mode(mode) if mode is not None else get_mode()
    if selected is Mode.SMOOTH:
        return (
            "Relaxed, warm, a little playful, and unhurried, like a late-night radio host. "
            "Keep every word clear. Say file names naturally and read line numbers at an easy pace. "
            "Do not add words or change the provided text."
        )
    return (
        "Clear, calm, neutral, and unhurried. Keep every word easy to understand. "
        "Say file names naturally and read line numbers plainly. "
        "Do not add words or change the provided text."
    )
