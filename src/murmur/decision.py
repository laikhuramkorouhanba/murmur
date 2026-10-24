"""Decide how to announce output using only its text."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
_PYTEST_ASSERT = re.compile(
    r"^\s*E\s+assert\s+(?P<actual>.+?)\s*==\s*(?P<expected>.+?)\s*$"
)
_SUCCESS = re.compile(
    r"\b(?:\d+\s+passed|success(?:fully)?|installed|finished|completed|done)\b",
    re.IGNORECASE,
)
_FILE_LOCATION = re.compile(
    r"(?<![\w./-])/?(?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9_]+:\d+(?::\d+)?"
)
_PYTHON_TRACEBACK_LOCATION = re.compile(r"File [\"']([^\"']+)[\"'], line (\d+)")
_FAILED_TEST = re.compile(r"\bFAILED\s+(?P<node>[^\s]+)")
_FAILED_COUNT = re.compile(r"\b(?P<count>\d+)\s+failed\b", re.IGNORECASE)
_PASSED_COUNT = re.compile(r"\b(?P<count>\d+)\s+passed\b", re.IGNORECASE)
_EXCEPTION_MESSAGE = re.compile(r"\b(?P<kind>[A-Za-z_]+(?:Error|Exception)):\s*(?P<message>.+)")
_COPIED_EXCEPTION_TEXT = re.compile(
    r"\b[A-Za-z_]*(?:Error|Exception):\s*[^.!?\n]+[.!?]?"
)


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


def natural_file_location(location: str) -> str:
    """Turn a printed path and line into words that sound clear when spoken."""
    match = re.match(r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)?$", location)
    if not match:
        return location.replace("_", " ").replace("/", ", ")
    filename = match.group("path").replace("\\", "/").rsplit("/", 1)[-1]
    if "." in filename:
        stem, extension = filename.rsplit(".", 1)
        filename = f"{stem.replace('_', ' ').replace('-', ' ')} dot {extension}"
    else:
        filename = filename.replace("_", " ").replace("-", " ")
    return f"{filename}, line {match.group('line')}"


def _failure_lead(text: str) -> str:
    count_match = _FAILED_COUNT.search(text)
    if count_match:
        count = int(count_match.group("count"))
    elif _FAILED_TEST.search(text):
        count = 1
    else:
        return "The command failed"
    failed = "one test failed" if count == 1 else f"{count} tests failed"
    passed_match = _PASSED_COUNT.search(text)
    if not passed_match:
        return failed[0].upper() + failed[1:]
    passed = int(passed_match.group("count"))
    passed_text = "one test passed" if passed == 1 else f"{passed} tests passed"
    return f"{passed_text[0].upper() + passed_text[1:]} and {failed}"


def _normalise_value(value: str) -> str:
    value = value.strip().strip("'\"")
    try:
        return format(Decimal(value).normalize(), "f")
    except InvalidOperation:
        return value


def _assertion_cause(text: str) -> tuple[str, str] | None:
    for line in text.splitlines():
        match = _PYTEST_ASSERT.match(line)
        if match:
            actual = _normalise_value(match.group("actual"))
            expected = _normalise_value(match.group("expected"))
            return expected, actual
    return None


def _natural_cause(evidence: ErrorEvidence) -> str:
    line = evidence.main_error_line or ""
    match = _EXCEPTION_MESSAGE.search(line)
    if match:
        kind = match.group("kind")
        message = match.group("message").lower()
        if kind == "TypeError" and "not enough arguments for format string" in message:
            return "the formatter was given too few arguments"
        if kind == "FileNotFoundError":
            return "the requested file could not be found"
        if kind == "PermissionError":
            return "the operation was blocked by permissions"
        if kind == "ModuleNotFoundError":
            return "a required Python module could not be found"
        if kind == "NameError":
            return "the code used a name that was not defined"
        return f"the command raised a {kind}"
    lowered = line.lower()
    if "permission denied" in lowered:
        return "the operation was blocked by permissions"
    if "command not found" in lowered:
        return "the shell could not find that command"
    if "no such file" in lowered:
        return "the requested file could not be found"
    return "the command reported an error"


def fallback_speech_summary(text: str) -> str:
    """Make a short, natural fallback without reading exact output lines aloud."""
    evidence = find_error_evidence(text)
    assertion = _assertion_cause(text)
    location = natural_file_location(evidence.file_locations[-1]) if evidence.file_locations else None
    lead = _failure_lead(text)
    if assertion:
        expected, actual = assertion
        if location:
            return f"{lead} in {location}: it expected {expected} but got {actual}."
        return f"{lead}: it expected {expected} but got {actual}."
    cause = _natural_cause(evidence)
    if location:
        return f"{lead} because {cause}. The error is in {location}."
    return f"{lead} because {cause}."


def speech_failure_summary(text: str, summary: str) -> str:
    """Keep one natural location and the cause, without speaking raw error lines."""
    evidence = find_error_evidence(text)
    assertion = _assertion_cause(text)
    location = natural_file_location(evidence.file_locations[-1]) if evidence.file_locations else None
    lead = _failure_lead(text)

    if assertion:
        expected, actual = assertion
        if location:
            return f"{lead} in {location}: it expected {expected} but got {actual}."
        return f"{lead}: it expected {expected} but got {actual}."

    facts = summary.strip()
    for exact_line in (evidence.main_error_line,):
        if exact_line:
            facts = facts.replace(exact_line, "")
    facts = _COPIED_EXCEPTION_TEXT.sub("", facts)
    facts = _FILE_LOCATION.sub("", facts)
    if location:
        facts = facts.replace(f"The error is in {location}.", "")
        facts = facts.replace(f"The failure is in {location}.", "")
        facts = re.sub(
            rf"\b(?:at|in|near|from)\s+{re.escape(location)}\b",
            "",
            facts,
            flags=re.IGNORECASE,
        )
        facts = re.sub(re.escape(location), "", facts, flags=re.IGNORECASE)
    facts = re.sub(r"\b(?:at|in|near|from)\s*(?=[,.:;!?]|$)", " ", facts, flags=re.IGNORECASE)
    facts = re.sub(r"\s+([,.:;!?])", r"\1", facts)
    facts = re.sub(r"\s{2,}", " ", facts).strip(" \t\r\n,;:-")
    if not facts:
        return fallback_speech_summary(text)
    if not re.search(r"\b(?:failed|error|exception)\b", facts, re.IGNORECASE):
        facts = f"{lead} because {facts[0].lower()}{facts[1:]}"
    if location:
        return f"{facts.rstrip('. ')}. The error is in {location}."
    return facts
