"""Read a command's whole output aloud, word for word.

Nothing is summarised or left out. We only change how places in code are written,
so the voice says them clearly: "shop.py:12:5" becomes "shop.py, line 12, column 5".
Long output is cut into pieces at line breaks, so speech starts quickly and can
be stopped between pieces.
"""

import re

MAX_PIECE_CHARS = 1200  # well under the speech model's limit, and quick to start

# A file name with an extension, then :line and maybe :column, like src/app.py:120:5.
_PLACE_IN_CODE = re.compile(r"(?P<path>[\w./~+-]+\.[A-Za-z]\w{0,9}):(?P<line>\d+)(?::(?P<column>\d+))?")


def _say_place(match: re.Match) -> str:
    path = match["path"]
    if path.startswith("//"):
        return match[0]  # part of a web address like https://example.com:443, not a file
    spoken = f"{path}, line {match['line']}"
    if match["column"]:
        spoken += f", column {match['column']}"
    return spoken


def speakable(text: str) -> str:
    """The same words, with file:line:column spelled out and blank lines dropped."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(_PLACE_IN_CODE.sub(_say_place, line) for line in lines)


def pieces(text: str, limit: int = MAX_PIECE_CHARS) -> list[str]:
    """Cut text into pieces of at most `limit` characters, at line breaks where possible."""
    result: list[str] = []
    current = ""
    for line in text.splitlines():
        while len(line) > limit:
            # One very long line: cut at the last space that fits (or hard, if there's none).
            cut = line.rfind(" ", 0, limit + 1)
            cut = cut if cut > 0 else limit
            if current:
                result.append(current)
                current = ""
            result.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        if current and len(current) + 1 + len(line) > limit:
            result.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        result.append(current)
    return [piece for piece in result if piece.strip()]
