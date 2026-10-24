"""Runtime personality modes for spoken summaries."""

from enum import Enum
import re
import threading


class Mode(str, Enum):
    SMOOTH = "Smooth"
    FOCUSED = "Focused"


_LOCK = threading.Lock()
_MODE = Mode.SMOOTH
_FAILURE_STREAK = 0
_LAST_OPENER: str | None = None

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
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def get_mode() -> Mode:
    with _LOCK:
        return _MODE


def set_mode(mode: Mode | str) -> None:
    global _MODE
    with _LOCK:
        _MODE = Mode(mode)


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
    global _LAST_OPENER
    with _LOCK:
        choices = tuple(option for option in options if option != _LAST_OPENER) or options
        _LAST_OPENER = choices[0]
        return _LAST_OPENER


def style_summary(facts: str, *, failed: bool) -> str:
    """Add a small Smooth flourish, or leave Focused wording untouched."""
    mode = get_mode()
    if mode is Mode.FOCUSED:
        return facts

    if failed and failure_streak() >= 3:
        opener = _next_opener(_ENCOURAGING_OPENERS)
        return f"{opener} {facts}"

    if failed:
        opener = _next_opener(_PLAYFUL_OPENERS)
        flourish = (
            " Nothing we can't smooth out."
            if len(_SENTENCE_BOUNDARY.split(facts.strip())) <= 1
            else ""
        )
        return f"{opener} {facts}{flourish}"

    opener = _next_opener(_SUCCESS_OPENERS)
    return f"{opener} {facts}"


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
