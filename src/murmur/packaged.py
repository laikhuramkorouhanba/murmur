"""Small fixes that only matter inside the built Murmur.app.

When you double-click an app, there's no terminal:
- Anything Murmur prints would vanish, so we write it to a log file instead.
- macOS gives apps a very short PATH (/usr/bin:/bin:/usr/sbin:/sbin), so tools
  installed with Homebrew, like `codex`, can't be found. We add the usual places back.
"""

import os
import sys
import time
from pathlib import Path

from murmur import config

LOG_PATH = Path.home() / "Library" / "Logs" / "Murmur" / "murmur.log"
LOG_MAX_BYTES = 5_000_000  # start a fresh log past this, keeping one old copy

# Where Homebrew (Intel and Apple silicon), pipx/uv tools, and npm usually put programs.
EXTRA_PATHS = (
    "/usr/local/bin",
    "/opt/homebrew/bin",
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".npm-global" / "bin"),
)


def add_tool_paths(environ: dict[str, str] = os.environ) -> None:
    """Put the usual tool folders on PATH, after whatever is already there."""
    current = [part for part in environ.get("PATH", "").split(os.pathsep) if part]
    missing = [path for path in EXTRA_PATHS if path not in current]
    environ["PATH"] = os.pathsep.join(current + missing)


def send_output_to_log(path: Path = LOG_PATH) -> None:
    """Write everything Murmur prints (and every error) to the log file, line by line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        path.replace(path.with_suffix(".old.log"))
    log = open(path, "a", encoding="utf-8", buffering=1)  # buffering=1: each line is saved straight away
    sys.stdout = sys.stderr = log
    print(f"\n===== Murmur started {time.strftime('%Y-%m-%d %H:%M:%S')} =====")


def prepare() -> None:
    """Call first thing in main(). Does nothing when running with `uv run`."""
    if not config.is_packaged():
        return
    add_tool_paths()
    try:
        send_output_to_log()
    except OSError:
        pass  # no log is better than no app
