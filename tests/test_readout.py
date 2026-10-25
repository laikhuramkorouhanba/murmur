"""Reading the full output: same words, places in code said clearly, cut into pieces."""

import pytest

from murmur import readout


@pytest.mark.parametrize(
    "raw, spoken",
    [
        ("src/murmur/app.py:120:5: E501 line too long", "src/murmur/app.py, line 120, column 5: E501 line too long"),
        ("shop.py:9: AssertionError", "shop.py, line 9: AssertionError"),
        ("FAILED shop_demo_check.py::test_total", "FAILED shop_demo_check.py::test_total"),  # no line number here
        ('  File "shop.py", line 9, in order_total', '  File "shop.py", line 9, in order_total'),
        ("Listening on http://localhost:8000", "Listening on http://localhost:8000"),
        ("See https://example.com:443/docs", "See https://example.com:443/docs"),
        ("Started at 12:30:05", "Started at 12:30:05"),
        ("./web/index.tsx:3:14 - error TS2304", "./web/index.tsx, line 3, column 14 - error TS2304"),
    ],
)
def test_places_in_code_are_spelled_out_and_nothing_else_changes(raw: str, spoken: str) -> None:
    assert readout.speakable(raw) == spoken


def test_every_word_is_kept_and_blank_lines_are_dropped() -> None:
    text = "collected 3 items\n\n   \nshop.py:9: AssertionError\nassert 18.40 == 21.60  \n"

    assert readout.speakable(text) == "collected 3 items\nshop.py, line 9: AssertionError\nassert 18.40 == 21.60"


def test_short_output_is_one_piece() -> None:
    assert readout.pieces("one\ntwo") == ["one\ntwo"]


def test_long_output_is_cut_at_line_breaks_and_nothing_is_lost() -> None:
    lines = [f"line {number} of the build log" for number in range(200)]

    chunks = readout.pieces("\n".join(lines), limit=100)

    assert all(len(chunk) <= 100 for chunk in chunks)
    assert "\n".join(chunks).splitlines() == lines


def test_one_very_long_line_is_cut_at_spaces() -> None:
    words = " ".join(f"word{number}" for number in range(100))

    chunks = readout.pieces(words, limit=50)

    assert all(len(chunk) <= 50 for chunk in chunks)
    assert " ".join(chunks).split() == words.split()


def test_a_long_line_with_no_spaces_is_still_cut() -> None:
    chunks = readout.pieces("x" * 130, limit=50)

    assert [len(chunk) for chunk in chunks] == [50, 50, 30]
