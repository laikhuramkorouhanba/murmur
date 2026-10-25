from pathlib import Path

import pytest

from murmur.decision import (
    AutoAction,
    auto_action,
    Action,
    decide,
    fallback_speech_summary,
    find_error_evidence,
    natural_file_location,
    speech_failure_summary,
)
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


def test_speech_summary_names_failure_location_and_assertion_cause_naturally() -> None:
    result = speech_failure_summary(sample("pytest_fail.txt"), "The test failed.")

    assert result == (
        "One test failed in failing check dot py, line 6: "
        "it expected 19.8 but got 18."
    )
    assert "demo/failing_check.py:6" not in result
    assert "AssertionError" not in result


def test_offline_failure_summary_explains_type_error_in_natural_path_form() -> None:
    original = sample("zsh_percent_in_traceback.txt")
    result = fallback_speech_summary(original)

    assert result == (
        "One test failed because the formatter was given too few arguments. "
        "The error is in app dot py, line 2."
    )
    assert "app.py:2" not in result


def test_naturalizes_file_name_without_speaking_directory_punctuation() -> None:
    assert natural_file_location("demo/failing_check.py:6") == "failing check dot py, line 6"


def test_sanitizes_a_machine_location_from_model_text_and_adds_it_once() -> None:
    original = "E       TypeError: bad value\napp.py:2: TypeError"
    result = speech_failure_summary(
        original,
        "The formatter received a bad value at app.py:2.",
    )

    assert result == (
        "The command failed because the formatter received a bad value. "
        "The error is in app dot py, line 2."
    )


def test_failure_summary_includes_passed_and_failed_test_counts() -> None:
    original = (
        "3 passed, 1 failed\n"
        "E       assert 18.0 == 19.8\n"
        "demo/failing_check.py:6: AssertionError"
    )

    result = fallback_speech_summary(original)

    assert result == (
        "3 tests passed and one test failed in failing check dot py, line 6: "
        "it expected 19.8 but got 18."
    )


def test_spoken_fallback_does_not_read_a_copied_exception_line_verbatim() -> None:
    original = sample("zsh_percent_in_traceback.txt")
    exact_message = "TypeError: not enough arguments for format string"

    result = speech_failure_summary(original, exact_message)

    assert result == fallback_speech_summary(original)
    assert exact_message not in result
    assert "app.py:2" not in result


def test_normalizing_an_already_natural_fallback_does_not_repeat_its_location() -> None:
    original = sample("zsh_percent_in_traceback.txt")
    fallback = fallback_speech_summary(original)

    assert speech_failure_summary(original, fallback) == fallback


def test_short_output_without_success_or_error_is_spoken_as_written() -> None:
    assert decide("/Users/laikhuram/project") is Action.SPEAK_EXACTLY


def test_long_output_is_summarised_even_without_error_words() -> None:
    assert decide("x" * 601) is Action.SUMMARISE



@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.5, AutoAction.SILENT),
        (1.9, AutoAction.SILENT),
        (2.0, AutoAction.SOFT_SOUND),
        (14.9, AutoAction.SOFT_SOUND),
        (15.0, AutoAction.DONE_LINE),
        (300.0, AutoAction.DONE_LINE),
    ],
)
def test_successful_commands_follow_the_table(seconds, expected) -> None:
    passing = (SAMPLES / "pytest_pass.txt").read_text(encoding="utf-8")

    assert auto_action(passing, seconds) is expected


@pytest.mark.parametrize("seconds", [0.5, 3.0, 20.0])
def test_errors_are_always_announced_however_long_they_took(seconds) -> None:
    failing = (SAMPLES / "pytest_fail.txt").read_text(encoding="utf-8")

    assert auto_action(failing, seconds) is AutoAction.ANNOUNCE


def test_a_long_command_with_no_output_gets_the_done_line() -> None:
    assert auto_action("", 20.0) is AutoAction.DONE_LINE


def test_assertion_error_from_a_file_pytest_did_not_rewrite() -> None:
    # The demo's shop_demo_check.py isn't named test_*.py, so pytest prints
    # "AssertionError: assert ..." and Decimal('...') values (found in the live test).
    from murmur.decision import speech_failure_summary

    output = (
        "F    [100%]\n"
        "E       AssertionError: assert Decimal('18.40') == Decimal('21.60')\n"
        "E        +  where Decimal('18.40') = order_total([(Decimal('20.00'), 1)], Decimal('0.08'))\n"
        "\n"
        "shop_demo_check.py:9: AssertionError\n"
        "FAILED shop_demo_check.py::test_order_total_adds_sales_tax - AssertionError: assert Decimal('18.40') == Decimal('21.60')\n"
        "1 failed in 0.10s"
    )

    spoken = speech_failure_summary(output, "The command failed. Lots of words.")

    assert "it expected 21.60 but got 18.40" in spoken
    assert "Decimal" not in spoken
    assert "The command failed" not in spoken
