from pathlib import Path

import pytest

from murmur.decision import (
    AutoAction,
    auto_action,
    brief_failure,
    has_exact_cause,
    is_short_output,
    line_count_line,
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
        ("pytest_pass.txt", AutoAction.READ_OUTPUT),
        ("progress.txt", AutoAction.READ_OUTPUT),
        ("table.txt", AutoAction.READ_OUTPUT),
        ("whitespace.txt", AutoAction.READ_OUTPUT),
        ("pytest_fail.txt", AutoAction.ANNOUNCE),
        ("decorations.txt", AutoAction.ANNOUNCE),
        ("zsh_history.txt", AutoAction.ANNOUNCE),
        ("zsh_percent_in_traceback.txt", AutoAction.ANNOUNCE),
    ],
)
def test_auto_announce_for_every_saved_sample(name: str, expected: AutoAction) -> None:
    assert auto_action(sample(name), 1.0) is expected


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


def test_ten_lines_are_short_and_eleven_are_not() -> None:
    assert is_short_output("\n".join(f"line {n}" for n in range(10)))
    assert not is_short_output("\n".join(f"line {n}" for n in range(11)))
    assert not is_short_output("x" * 801)  # a few very long lines aren't short either


LONG_SUCCESS = "\n".join(f"compiled module {n}" for n in range(42))


@pytest.mark.parametrize(
    ("detail", "text", "seconds", "expected"),
    [
        # Errors and short output (the default)
        ("short", "README.md  shop.py", 0.3, AutoAction.READ_OUTPUT),
        ("short", LONG_SUCCESS, 0.3, AutoAction.LINE_COUNT),
        ("short", "", 0.3, AutoAction.SILENT),
        ("short", "", 20.0, AutoAction.DONE_LINE),
        # Errors only: successes get the pass sound at most
        ("errors", "README.md  shop.py", 0.3, AutoAction.SILENT),
        ("errors", "README.md  shop.py", 5.0, AutoAction.PASS_SOUND),
        ("errors", LONG_SUCCESS, 5.0, AutoAction.PASS_SOUND),
        ("errors", "", 0.3, AutoAction.SILENT),
        # Everything: all of it, word for word
        ("everything", LONG_SUCCESS, 0.3, AutoAction.READ_OUTPUT),
        ("everything", "", 0.3, AutoAction.SILENT),
    ],
)
def test_successful_commands_follow_the_table(detail, text, seconds, expected) -> None:
    assert auto_action(text, seconds, detail) is expected


def test_long_output_is_counted_not_read() -> None:
    assert line_count_line(LONG_SUCCESS) == "Done, 42 lines."
    assert line_count_line("one\n\n") == "Done, 1 line."


@pytest.mark.parametrize("detail", ["errors", "short", "everything"])
@pytest.mark.parametrize("seconds", [0.5, 3.0, 20.0])
def test_errors_are_always_announced_however_long_they_took(seconds, detail) -> None:
    failing = (SAMPLES / "pytest_fail.txt").read_text(encoding="utf-8")

    assert auto_action(failing, seconds, detail) is AutoAction.ANNOUNCE


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


def test_brief_failure_is_status_then_place_then_exact_cause() -> None:
    demo_output = (
        "F                                                                        [100%]\n"
        "E       AssertionError: assert Decimal('18.40') == Decimal('21.60')\n"
        "shop_demo_check.py:9: AssertionError\n"
        "1 failed in 0.05s"
    )

    assert brief_failure(demo_output) == "Failed. shop demo check dot py, line 9. Expected 21.60, got 18.40."
    # VoiceOver also drives braille displays, so it gets the real file name.
    assert brief_failure(demo_output, exact_names=True) == "Failed. shop_demo_check.py, line 9. Expected 21.60, got 18.40."


def test_brief_failure_keeps_the_exact_error_line() -> None:
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "/app/main.py", line 3, in <module>\n'
        "    import requests\n"
        "ModuleNotFoundError: No module named 'requests'"
    )

    assert brief_failure(traceback) == "Failed. main dot py, line 3. ModuleNotFoundError: No module named 'requests'"
    assert has_exact_cause(traceback)  # so no summary request is needed
    assert brief_failure("zsh: command not found: pyhton") == "Failed. zsh: command not found: pyhton"
    # The place isn't said twice when the error line starts with it.
    assert brief_failure("main.c:5:3: error: expected ';'") == "Failed. main dot c, line 5. error: expected ';'"


def test_brief_failure_counts_several_failed_tests() -> None:
    assert sample("pytest_fail.txt")  # the sample exists
    text = "FAILED a.py::one\nFAILED a.py::two\nE   assert 1 == 2\na.py:4: AssertionError\n2 failed in 0.1s"

    assert brief_failure(text).startswith("Failed, 2 tests. a dot py, line 4. Expected 2, got 1.")


def test_brief_failure_uses_the_summary_only_when_the_output_names_no_cause() -> None:
    puzzle = "something went wrong somewhere\nerror"

    assert not has_exact_cause("tests.py:3: AssertionError")
    assert brief_failure("tests.py:3: AssertionError", "The totals didn't match.") == (
        "Failed. tests dot py, line 3. The totals didn't match."
    )
    assert brief_failure(puzzle, "").startswith("Failed.")
