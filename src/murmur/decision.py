"""Decide how to announce output using only its text."""

import re
from dataclasses import dataclass
from enum import Enum

from murmur import config


class Action(str, Enum):
    SOFT_SOUND = "soft_sound"
    SPEAK_EXACTLY = "speak_exactly"
    SUMMARISE = "summarise"


@dataclass(frozen=True)
class ErrorEvidence:
    """The main error line and exact file-and-line references in output."""

    main_error_line: str | None
    file_locations: tuple[str, ...]


_ERROR_WORD = re.compile(
    r"\b(?:error|failed|traceback|exception|fatal|assertionerror)\b"
    r"|command not found|no such file|permission denied",
    re.IGNORECASE,
)
_EXCEPTION_NAME = re.compile(r"\b[A-Za-z_]*(?:Error|Exception)\b")
_PYTEST_ASSERT = re.compile(r"^\s*E\s+assert\b")
_SUCCESS = re.compile(
    r"\b(?:\d+\s+passed|success(?:fully)?|installed|finished|completed|done)\b",
    re.IGNORECASE,
)
_FILE_LOCATION = re.compile(
    r"(?<![\w./-])/?(?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9_]+:\d+(?::\d+)?"
)
_PYTHON_TRACEBACK_LOCATION = re.compile(r"File [\"']([^\"']+)[\"'], line (\d+)")


def decide(text: str) -> Action:
    """Choose a soft cue, exact speech, or a short AI summary."""
    text = text.strip()
    if not text:
        return Action.SOFT_SOUND
    if contains_error(text) or len(text) > config.SUMMARY_TRIGGER_CHARS:
        return Action.SUMMARISE
    if _SUCCESS.search(text):
        return Action.SOFT_SOUND
    return Action.SPEAK_EXACTLY


def contains_error(text: str) -> bool:
    return _ERROR_WORD.search(text) is not None


def find_error_evidence(text: str) -> ErrorEvidence:
    """Find the main error line and each printed file:line reference."""
    lines = text.splitlines()
    exception_lines = [line for line in lines if _EXCEPTION_NAME.search(line)]
    assertion_lines = [line for line in lines if _PYTEST_ASSERT.search(line)]
    marked_lines = [line for line in lines if _ERROR_WORD.search(line)]

    if exception_lines:
        main_error_line = exception_lines[0]
    elif assertion_lines:
        main_error_line = assertion_lines[0]
    else:
        main_error_line = marked_lines[0] if marked_lines else None

    locations: list[str] = []
    for line in lines:
        locations.extend(_FILE_LOCATION.findall(line))
        locations.extend(
            f"{path}:{line_number}"
            for path, line_number in _PYTHON_TRACEBACK_LOCATION.findall(line)
        )

    return ErrorEvidence(main_error_line, tuple(dict.fromkeys(locations)))


def add_missing_exact_details(summary: str, original_text: str) -> str:
    """Append any exact error details the summary left out."""
    evidence = find_error_evidence(original_text)
    additions: list[str] = []
    for location in evidence.file_locations:
        if location not in summary:
            additions.append(f"Exact location: {location}")
    if evidence.main_error_line and evidence.main_error_line not in summary:
        additions.append(f"Exact error: {evidence.main_error_line}")
    if not additions:
        return summary
    return "\n".join(part for part in (summary.rstrip(), *additions) if part)


def exact_fallback_text(text: str) -> str:
    """Keep the key error details when a summary service is unavailable."""
    evidence = find_error_evidence(text)
    parts = [*evidence.file_locations]
    if evidence.main_error_line:
        parts.append(evidence.main_error_line)
    parts = list(dict.fromkeys(parts))
    if parts:
        return "\n".join(parts)
    return "\n".join(line for line in text.splitlines()[-3:] if line.strip())
