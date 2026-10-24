from pathlib import Path

import pytest

from murmur.decision import Action, add_missing_exact_details, decide, find_error_evidence
from murmur.cleaner import clean


SAMPLES = Path(__file__).parent / "samples"


def sample(name: str) -> str:
    return clean((SAMPLES / name).read_bytes().decode("utf-8"))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pytest_pass.txt", Action.SOFT_SOUND),
        ("progress.txt", Action.SOFT_SOUND),
        ("whitespace.txt", Action.SOFT_SOUND),
        ("zsh_percent_in_progress.txt", Action.SOFT_SOUND),
        ("table.txt", Action.SPEAK_EXACTLY),
        ("pytest_fail.txt", Action.SUMMARISE),
        ("decorations.txt", Action.SUMMARISE),
        ("zsh_history.txt", Action.SUMMARISE),
        ("zsh_percent_in_traceback.txt", Action.SUMMARISE),
    ],
)
def test_decides_for_every_saved_sample(name: str, expected: Action) -> None:
    assert decide(sample(name)) is expected


def test_finds_exact_error_and_file_line_evidence_in_pytest_sample() -> None:
    evidence = find_error_evidence(sample("pytest_fail.txt"))

    assert evidence.main_error_line == "demo/failing_check.py:6: AssertionError"
    assert evidence.file_locations == ("demo/failing_check.py:6",)


def test_finds_traceback_error_and_each_location() -> None:
    evidence = find_error_evidence(sample("zsh_percent_in_traceback.txt"))

    assert evidence.main_error_line == "E       TypeError: not enough arguments for format string"
    assert evidence.file_locations == (
        "tests/test_greet.py:4",
        "app.py:2",
    )


def test_adds_exact_error_line_and_missing_locations_to_summary() -> None:
    original = sample("pytest_fail.txt")

    summary = add_missing_exact_details("The test failed.", original)

    assert summary == (
        "The test failed.\n"
        "Exact location: demo/failing_check.py:6\n"
        "Exact error: demo/failing_check.py:6: AssertionError"
    )


def test_does_not_duplicate_details_already_in_summary() -> None:
    original = sample("pytest_fail.txt")
    summary = "Failure at demo/failing_check.py:6: demo/failing_check.py:6: AssertionError"

    assert add_missing_exact_details(summary, original) == summary


def test_short_output_without_success_or_error_is_spoken_as_written() -> None:
    assert decide("/Users/laikhuram/project") is Action.SPEAK_EXACTLY


def test_long_output_is_summarised_even_without_error_words() -> None:
    assert decide("x" * 601) is Action.SUMMARISE
