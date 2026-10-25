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
    "auto_announce": True,
    "auto_detail": "short",  # errors, short (errors and short output) or everything
    "captions": False,
    "double_tap_key": "right_command",
    "engine": "mac",  # "mac" or "natural"
    "key_prompt_done": False,  # asked for the API key once already
    "speed": 0.5,  # the Speed slider, 0 (slowest) to 1 (fastest)
    "style": "Brief",  # a new name: the old "personality" setting is ignored, so everyone starts on Brief
    "voice": "marin",  # the Natural voice: marin or cedar
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


def migrate() -> None:
    """Bring a settings file from an older Murmur up to date. Run once at startup.

    - The VoiceOver engine is gone from the menu: "auto" and "voiceover" become the Mac voice.
    - Cedar is gone: the Natural voice is always Marin.
    - Speed presets ("fast" and so on) become slider positions.
    - Friendly, the auto-announce levels and other double-tap keys are gone:
      Brief, errors plus short output, and Right Command.
    """
    with _LOCK:
        data = _read()
        if not data:
            return
        before = dict(data)
        if data.get("engine", "mac") not in ("mac", "natural"):
            data["engine"] = "mac"
        if data.get("voice", "marin") != "marin":
            data["voice"] = "marin"
        speed = data.get("speed")
        if isinstance(speed, str) or isinstance(speed, bool):
            from murmur import config

            data["speed"] = config.OLD_SPEED_PRESETS.get(speed, DEFAULTS["speed"])
        for name, fixed in (("style", "Brief"), ("auto_detail", "short"), ("double_tap_key", "right_command")):
            if name in data:
                data[name] = fixed
        data.pop("personality", None)  # the oldest name for the style
        if data == before:
            return
        try:
            temporary = PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            temporary.replace(PATH)
            print("Murmur: brought your saved settings up to date.")
        except OSError as error:
            print(f"Murmur: could not update settings in {PATH} ({error}).")

