from pathlib import Path

from murmur.cleaner import clean


SAMPLES = Path(__file__).parent / "samples"


def read_sample_bytes(name: str) -> str:
    # Reading bytes keeps the progress sample's carriage returns intact.
    return (SAMPLES / name).read_bytes().decode("utf-8")


def test_removes_colour_and_keeps_the_final_progress_state() -> None:
    assert clean(read_sample_bytes("progress.txt")) == (
        "Downloading: 100%\nFinished installing package."
    )


def test_trims_line_ends_and_collapses_blank_lines() -> None:
    assert clean("first   \n\n\nsecond\t\n") == "first\n\nsecond"


def test_turns_box_drawing_table_into_plain_text() -> None:
    assert clean(read_sample_bytes("table.txt")) == (
        "Name | Version | Source\n"
        "murmur | 0.1.0 | workspace\n"
        "pytest | 9.1.1 | pypi"
    )


def test_keeps_error_file_name_and_text_exact() -> None:
    result = clean(read_sample_bytes("pytest_fail.txt"))

    assert "demo/failing_check.py:6: AssertionError" in result
    assert "E       assert 18.0 == 19.8" in result
