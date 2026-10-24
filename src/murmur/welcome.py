"""The short spoken welcome Murmur gives the very first time it starts."""

from murmur import config, hotkey, personality


def welcome_text(
    mode: personality.Mode | str | None = None,
    *,
    app_name: str | None = None,
    missing: list[str] | None = None,
) -> str:
    """Build the welcome: who Murmur is, the shortcuts, and every permission it needs."""
    selected = personality.Mode(mode) if mode is not None else personality.get_mode()
    app = app_name or hotkey.launching_app_name()
    missing = hotkey.missing_permissions() if missing is None else missing
    tap = hotkey.double_tap_label()

    if selected is personality.Mode.SMOOTH:
        intro = (
            "Hey there, I'm Murmur. I keep an ear on Terminal and tell you how your "
            "commands went, so you don't have to read the screen."
        )
        outro = "That's it. Replay welcome in the Murmur menu plays this again. Have fun, cool cat."
    else:
        intro = "Welcome to Murmur. It reads your last Terminal command and tells you how it went."
        outro = "To hear this again, choose Replay welcome in the Murmur menu."

    shortcuts = (
        f"To hear the last result, press {config.HOTKEY_SPOKEN}, or tap {tap}. "
        "Press either one again to stop the speech."
    )
    permissions = (
        "Murmur needs three macOS permissions, all in System Settings, then Privacy and Security. "
        f"One: Automation. Allow {app} to control Terminal, so Murmur can read the Terminal window. "
        f"Two and three: Accessibility, and Input Monitoring. Turn on {app} in both, "
        f"so the shortcuts work from any app. Then quit and reopen {app}."
    )
    if missing:
        status = f"Right now, {' and '.join(missing)} still needs turning on."
    else:
        status = "Accessibility and Input Monitoring are already on."

    return " ".join((intro, shortcuts, permissions, status, outro))
