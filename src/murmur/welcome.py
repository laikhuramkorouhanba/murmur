"""The short spoken welcome Murmur gives the very first time it starts."""

from murmur import config, hotkey, personality, watcher


def missing_permissions() -> list[str]:
    """List only permissions that macOS currently reports as unavailable."""
    missing = hotkey.missing_permissions()
    if watcher.automation_permission_missing():
        missing.insert(0, "Automation")
    return missing


def welcome_text(
    mode: personality.Mode | str | None = None,
    *,
    app_name: str | None = None,
    missing: list[str] | None = None,
) -> str:
    """Build a brief welcome with shortcut help and only missing permissions."""
    selected = personality.Mode(mode) if mode is not None else personality.get_mode()
    app = app_name or hotkey.launching_app_name()
    missing = missing_permissions() if missing is None else missing
    tap = hotkey.double_tap_label()

    if selected is personality.Mode.SMOOTH:
        intro = "Hey, Murmur here."
        outro = "Let's go, cool cat."
    else:
        intro = "Welcome to Murmur."
        outro = "Ready when you are."

    shortcuts = (
        f"Tap {tap} twice for results. Backup: {config.HOTKEY_SPOKEN}. "
        "Repeat either shortcut to stop speech."
    )
    permission_notes = []
    if "Automation" in missing:
        permission_notes.append(
            f"Missing Automation: allow {app} to control Terminal in "
            "System Settings → Privacy & Security → Automation."
        )
    keyboard_missing = [
        name for name in ("Accessibility", "Input Monitoring") if name in missing
    ]
    if keyboard_missing:
        names = " and ".join(keyboard_missing)
        permission_notes.append(
            f"Missing {names}; "
            f"enable {app} in System Settings → Privacy & Security."
        )

    return " ".join((intro, shortcuts, *permission_notes, outro))
