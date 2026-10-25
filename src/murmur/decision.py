"""Decide how to announce output using only its text."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum

from murmur import config


class AutoAction(str, Enum):
    """What auto-announce does when a command finishes on its own."""

    SILENT = "silent"
    PASS_SOUND = "pass_sound"  # just the pass sound, no words
    READ_OUTPUT = "read_output"  # the pass sound, then the output word for word
    LINE_COUNT = "line_count"  # the pass sound, then "Done, 42 lines."
    DONE_LINE = "done_line"  # a long command with no output: the pass sound, then "Done."
    ANNOUNCE = "announce"  # an error: the full announcement, same as pressing the hotkey


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
# "E   assert 18 == 19.8", or "E   AssertionError: assert ..." when pytest didn't
# rewrite the test (it only does that for files named like test_*.py).
_PYTEST_ASSERT = re.compile(
    r"^\s*E\s+(?:AssertionError:\s+)?assert\s+(?P<actual>.+?)\s*==\s*(?P<expected>.+?)\s*$"
)
_DECIMAL_VALUE = re.compile(r"Decimal\((['\"])(?P<value>[^'\"]+)\1\)")
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


def output_lines(text: str) -> int:
    """How many lines of output there are, not counting blank ones."""
    return sum(1 for line in text.splitlines() if line.strip())


def is_short_output(text: str) -> bool:
    """Short enough to read out word for word: about ten lines at most."""
    return output_lines(text) <= config.SHORT_OUTPUT_LINES and len(text.strip()) <= config.SHORT_OUTPUT_CHARS


def auto_detail() -> str:
    """The "Auto-announce" menu choice: "errors", "short" or "everything"."""
    from murmur import settings  # here, so this file stays free of saved state for its tests

    chosen = settings.get("auto_detail")
    return chosen if chosen in config.AUTO_DETAILS else "short"


def auto_action(text: str, seconds: float, detail: str = "short") -> AutoAction:
    """Pick what auto-announce does with a finished command.

    | Finished command               | Errors only | Errors and short output | Everything |
    | ------------------------------ | ----------- | ----------------------- | ---------- |
    | has an error (any length)      | announce    | announce                | announce   |
    | no output                      | silent      | silent                  | silent     |
    | no output, ran 15 s or more    | "Done."     | "Done."                 | "Done."    |
    | up to about 10 lines           | sound only* | read word for word      | read all   |
    | longer                         | sound only* | "Done, 42 lines."       | read all   |

    * successes quicker than 2 s stay silent with Errors only.
    """
    if contains_error(text):
        return AutoAction.ANNOUNCE
    if not text.strip():
        return AutoAction.DONE_LINE if seconds >= config.AUTO_LONG_SUCCESS_SECONDS else AutoAction.SILENT
    if detail == "errors":
        return AutoAction.SILENT if seconds < config.AUTO_SHORT_SECONDS else AutoAction.PASS_SOUND
    if detail == "everything" or is_short_output(text):
        return AutoAction.READ_OUTPUT
    return AutoAction.LINE_COUNT


def line_count_line(text: str) -> str:
    count = output_lines(text)
    return f"Done, {count} {'line' if count == 1 else 'lines'}."


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
    wrapped = _DECIMAL_VALUE.fullmatch(value.strip())
    if wrapped:
        return wrapped.group("value")  # Decimal('21.60') is said as written: 21.60
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


def has_assertion_cause(text: str) -> bool:
    """True when we can say "it expected X but got Y" ourselves, with no summary needed."""
    return _assertion_cause(text) is not None


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


def _exact_place(location: str) -> str:
    """shop_demo_check.py:9 as "shop_demo_check.py, line 9": the real name, for VoiceOver and braille."""
    match = re.match(r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)?$", location)
    if not match:
        return location
    return f"{match.group('path').replace(chr(92), '/').rsplit('/', 1)[-1]}, line {match.group('line')}"


def _exact_cause_line(evidence: ErrorEvidence) -> str | None:
    """The main error line as printed, minus pytest's "E" margin and any file:line we already said."""
    line = evidence.main_error_line
    if not line:
        return None
    line = re.sub(r"^\s*E\s+", "", line)
    line = _FILE_LOCATION.sub("", line)
    line = re.sub(r"^[\s:,-]+", "", line).strip()
    return line or None


def has_exact_cause(text: str) -> bool:
    """True when Brief can name the cause from the output itself, with no summary request."""
    evidence = find_error_evidence(text)
    if _assertion_cause(text) is not None:
        return True
    cause = _exact_cause_line(evidence)
    # "shop.py:9: AssertionError" on its own names no cause, so a summary still helps there.
    return cause is not None and not re.fullmatch(r"[A-Za-z_]*(?:Error|Exception)", cause)


def brief_failure(text: str, summary: str = "", *, exact_names: bool = False) -> str:
    """Brief style: "Failed.", then the file and line, then the exact cause. Nothing else.

    exact_names keeps the real file name (for VoiceOver and braille displays);
    otherwise it's said the way a voice reads it best, like "shop demo check dot py".
    """
    count_match = _FAILED_COUNT.search(text)
    count = int(count_match.group("count")) if count_match else 1
    parts = ["Failed." if count == 1 else f"Failed, {count} tests."]

    evidence = find_error_evidence(text)
    if evidence.file_locations:
        location = evidence.file_locations[-1]
        place = _exact_place(location) if exact_names else natural_file_location(location)
        parts.append(f"{place}.")

    assertion = _assertion_cause(text)
    cause = _exact_cause_line(evidence)
    if assertion:
        expected, actual = assertion
        parts.append(f"Expected {expected}, got {actual}.")
    elif has_exact_cause(text) and cause:
        parts.append(cause)
    elif summary.strip():
        parts.append(summary.strip())
    else:
        reason = _natural_cause(evidence)
        parts.append(f"{reason[0].upper()}{reason[1:]}.")
    return " ".join(parts)

