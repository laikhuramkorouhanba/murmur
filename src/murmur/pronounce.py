"""Say names the same way every time.

Text-to-speech voices guess at file names, and guess differently from one line to
the next: OpenAI said README as "reeeadme" once and "ridme" the next time. So before
any speech, the words that voices trip over are rewritten into plain words:

    README.md       → read me dot M D
    shop_demo.py    → shop demo dot pie
    pyproject.toml  → pie project dot tommel

Only what's spoken changes. Captions and printed text keep the real names.
"""

import re

# File name endings: ".py" in "shop.py", and "dot py" as Murmur already says it.
# Letter by letter (with spaces) where a voice would otherwise try to read a word.
EXTENSIONS = {
    "py": "pie",
    "pyc": "pie C",
    "pyi": "pie I",
    "ipynb": "I pie N B",
    "md": "M D",
    "txt": "text",
    "json": "jason",
    "jsonl": "jason L",
    "toml": "tommel",
    "yaml": "yammel",
    "yml": "yammel",
    "ini": "I N I",
    "cfg": "config",
    "env": "env",
    "sh": "S H",
    "zsh": "Z S H",
    "bash": "bash",
    "js": "J S",
    "mjs": "M J S",
    "ts": "T S",
    "tsx": "T S X",
    "jsx": "J S X",
    "html": "H T M L",
    "css": "C S S",
    "scss": "S C S S",
    "c": "C",
    "h": "H",
    "cpp": "C P P",
    "hpp": "H P P",
    "rs": "R S",
    "go": "go",
    "rb": "R B",
    "java": "java",
    "kt": "K T",
    "swift": "swift",
    "sql": "S Q L",
    "csv": "C S V",
    "xml": "X M L",
    "svg": "S V G",
    "png": "P N G",
    "jpg": "J peg",
    "jpeg": "J peg",
    "pdf": "P D F",
    "lock": "lock",
    "log": "log",
    "diff": "diff",
    "wav": "wave",
    "app": "app",
}

# Whole words (any capitals), said as written on the right.
WORDS = {
    "readme": "read me",
    "changelog": "change log",
    "makefile": "make file",
    "dockerfile": "docker file",
    "gitignore": "git ignore",
    "pytest": "pie test",
    "pyproject": "pie project",
    "pypi": "pie P I",
    "mypy": "my pie",
    "numpy": "num pie",
    "pycache": "pie cache",
    "venv": "V env",
    "uv": "U V",
    "npm": "N P M",
    "npx": "N P X",
    "pip": "pip",
    "stdout": "standard out",
    "stderr": "standard error",
    "stdin": "standard in",
    "todo": "to do",
    "cli": "C L I",
    "api": "A P I",
    "url": "U R L",
    "sql": "S Q L",
    "json": "jason",
    "yaml": "yammel",
    "toml": "tommel",
    "regex": "rejex",
    "sudo": "sue doo",
    "macos": "mac O S",
    "zsh": "Z S H",
    "args": "args",
    "kwargs": "keyword args",
    "init": "init",
    "py": "pie",
}

# "shop.py": a dot between a name and a known ending. Not "21.60" or "example.com".
_DOTTED_ENDING = re.compile(
    r"(?<=[A-Za-z0-9_\-)])\.(?P<ext>" + "|".join(sorted(EXTENSIONS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
# ".env" or ".gitignore" on its own, at the start of a word.
_LEADING_DOT = re.compile(r"(?<![\w.])\.(?P<name>[A-Za-z][\w-]*)")
# "dot py", the way Murmur's own summaries already say file names.
_SPOKEN_ENDING = re.compile(
    r"\bdot (?P<ext>" + "|".join(sorted(EXTENSIONS, key=len, reverse=True)) + r")\b", re.IGNORECASE
)
_WORD = re.compile(r"[A-Za-z]+")


def _ending(match: re.Match) -> str:
    return f" dot {EXTENSIONS[match['ext'].lower()]}"


def _word(match: re.Match) -> str:
    return WORDS.get(match[0].lower(), match[0])


def spoken(text: str) -> str:
    """The same text, with file names and tricky words written the way they should sound."""
    text = _DOTTED_ENDING.sub(_ending, text)
    text = _SPOKEN_ENDING.sub(_ending, text)
    text = _LEADING_DOT.sub(lambda m: f"dot {m['name']}", text)
    # Underscores (and the double ones in __init__) are word gaps, not "underscore".
    text = re.sub(r"_+", " ", text)
    text = _WORD.sub(_word, text)
    # CamelCase names like ModuleNotFoundError: one word per hump, the same way every time.
    text = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip(" \t")
