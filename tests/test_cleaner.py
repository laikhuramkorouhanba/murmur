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
