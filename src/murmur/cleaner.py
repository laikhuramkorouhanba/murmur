"""Small, predictable cleanup for text copied from a terminal."""

import re


_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_TABLE_SEPARATORS = re.compile(r"[│║┃]")
_DECORATION_ONLY = re.compile(r"^[=_*-]{3,}$")
_WRAPPED_DECORATION = re.compile(r"^([=_*-])\1{2,}\s*(.*?)\s*\1{3,}$")


def _is_box_border(line: str) -> bool:
    """Tell apart a table border from a row with real text."""
    visible = [char for char in line if not char.isspace()]
    return bool(visible) and all("\u2500" <= char <= "\u257f" for char in visible)


def _clean_table_row(line: str) -> str:
    """Turn one vertical table row into text with simple separators."""
    cells = [cell.strip() for cell in _TABLE_SEPARATORS.split(line)]
    while cells and not cells[0]:
        cells.pop(0)
    while cells and not cells[-1]:
        cells.pop()
    return " | ".join(cells)


def clean(text: str) -> str:
    """Remove terminal colour and redraw noise while keeping useful text."""
    text = _ANSI_ESCAPE.sub("", text)
    text = text.replace("\r\n", "\n")

    cleaned_lines: list[str] = []
    previous_was_blank = False

    for line in text.split("\n"):
        # A carriage return redraws the current terminal line from its start.
        line = line.split("\r")[-1].rstrip()
        if _DECORATION_ONLY.fullmatch(line):
            continue
        wrapped = _WRAPPED_DECORATION.fullmatch(line)
        if wrapped:
            line = wrapped.group(2)
        if _is_box_border(line):
            continue
        if _TABLE_SEPARATORS.search(line):
            line = _clean_table_row(line)
        is_blank = not line
        if is_blank and previous_was_blank:
            continue
        cleaned_lines.append(line)
        previous_was_blank = is_blank

    while cleaned_lines and not cleaned_lines[0]:
        cleaned_lines.pop(0)
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()

    return "\n".join(cleaned_lines)
