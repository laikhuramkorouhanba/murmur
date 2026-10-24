"""Remember the user's menu choices in a tiny JSON file.

The file lives at ~/.murmur/settings.json. If it is missing or broken, we just
use the defaults, so a bad file can never stop Murmur from starting.
"""

import json
import threading
from pathlib import Path
from typing import Any


PATH = Path.home() / ".murmur" / "settings.json"

DEFAULTS: dict[str, Any] = {
    "double_tap_key": "right_command",
    "voice": "marin",
    "welcome_done": False,
}

_LOCK = threading.Lock()


def _read() -> dict[str, Any]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get(name: str) -> Any:
    """Return a saved setting, or its default if nothing is saved yet."""
    with _LOCK:
        return _read().get(name, DEFAULTS[name])


def set(name: str, value: Any) -> None:
    """Save one setting and keep all the others as they were."""
    if name not in DEFAULTS:
        raise KeyError(name)
    with _LOCK:
        data = _read()
        data[name] = value
        try:
            PATH.parent.mkdir(parents=True, exist_ok=True)
            # Write to a side file first, then swap it in, so a crash never leaves half a file.
            temporary = PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            temporary.replace(PATH)
        except OSError as error:
            print(f"Murmur: could not save settings to {PATH} ({error}). Using it for this session only.")
