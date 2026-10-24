"""Small, predictable cleanup for text copied from a terminal."""

import re


_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def clean(text: str) -> str:
    """Remove terminal colour and redraw noise while keeping useful text."""
    text = _ANSI_ESCAPE.sub("", text)
    text = text.replace("\r\n", "\n")

    cleaned_lines: list[str] = []
    previous_was_blank = False

    for line in text.split("\n"):
        # A carriage return redraws the current terminal line from its start.
        line = line.split("\r")[-1].rstrip()
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
