"""Read the selected tab's text from an already-open Terminal.app window."""

import subprocess


_APPLE_SCRIPT = '''
tell application "Terminal"
    if (count of windows) is 0 then
        return "MURMUR_NO_WINDOWS"
    end if
    return contents of selected tab of front window
end tell
'''
_NO_WINDOWS = "MURMUR_NO_WINDOWS"
_TIMEOUT_SECONDS = 5


def get_terminal_text() -> str:
    """Return Terminal's selected-tab scrollback, or explain why it is unavailable."""
    try:
        running = subprocess.run(
            ["pgrep", "-x", "Terminal"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Murmur can read Terminal.app only on macOS.") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Checking whether Terminal.app is open timed out.") from error

    if running.returncode != 0:
        raise RuntimeError("Terminal.app is not running. Open Terminal.app and try again.")

    try:
        result = subprocess.run(
            ["osascript", "-e", _APPLE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Murmur could not find osascript. This feature needs macOS.") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Reading Terminal.app took longer than 5 seconds.") from error

    if result.returncode != 0:
        details = result.stderr.strip()
        if "-1743" in details or "not authorized" in details.lower():
            raise RuntimeError(
                "macOS blocked Murmur from reading Terminal.app. Allow Terminal control in "
                "System Settings → Privacy & Security → Automation."
            )
        raise RuntimeError(f"Could not read Terminal.app: {details or 'AppleScript failed.'}")

    if result.stdout.strip() == _NO_WINDOWS:
        raise RuntimeError("Terminal.app has no windows open. Open a window and try again.")

    return result.stdout
