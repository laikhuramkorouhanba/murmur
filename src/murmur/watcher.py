"""Read the selected tab's history from an already-open Terminal.app window."""

import re
import subprocess
from dataclasses import dataclass


_RUNNING_SCRIPT = 'application "Terminal" is running'
_AUTOMATION_CHECK_SCRIPT = '''
if application "Terminal" is running then
    tell application "Terminal" to get count of windows
else
    return 0
end if
'''
_HISTORY_SCRIPT = '''
tell application "Terminal"
    if (count of windows) is 0 then
        return "MURMUR_NO_WINDOWS"
    end if
    return history of selected tab of front window
end tell
'''
_NO_WINDOWS = "MURMUR_NO_WINDOWS"
_NOT_RUNNING = "MURMUR_NOT_RUNNING"
_NO_TAB = "MURMUR_NO_TAB"

# One quick question to Terminal, asked about once a second by auto-announce.
# It checks "is Terminal running" first, so it never launches Terminal by itself.
# The answer looks like: /dev/ttys003<tab>true<tab>login,-zsh,python3
_SNAPSHOT_SCRIPT = '''
if application "Terminal" is not running then
    return "MURMUR_NOT_RUNNING"
end if
-- Inside "tell Terminal", the word tab means a Terminal tab, so name the tab character first.
set sep to character id 9
tell application "Terminal"
    if (count of windows) is 0 then
        return "MURMUR_NO_WINDOWS"
    end if
    try
        set theTab to selected tab of front window
        set AppleScript's text item delimiters to ","
        set names to (processes of theTab) as text
        set AppleScript's text item delimiters to ""
        return (tty of theTab) & sep & ((busy of theTab) as text) & sep & names
    on error
        return "MURMUR_NO_TAB"
    end try
end tell
'''


@dataclass(frozen=True)
class TabSnapshot:
    """What the front Terminal tab is doing right now."""

    tty: str  # like /dev/ttys003; it tells tabs apart
    busy: bool  # true while a command is running
    processes: tuple[str, ...]  # like ("login", "-zsh", "python3")
_TIMEOUT_SECONDS = 5
# A zsh prompt looks like "user@host folder % " (or just "folder % ").
# We match that whole shape, so a "%" inside normal output is not mistaken for a prompt.
_ZSH_PROMPT = re.compile(r"^(?:[\w.+-]+@[\w.-]+ [^%]*|\S+) %(?P<rest>(?:[ \t].*)?)$")


def last_command_output(text: str) -> str:
    """Keep text after the latest recognizable zsh percent prompt."""
    lines = text.split("\n")
    last_command_line: int | None = None

    for index, line in enumerate(lines):
        match = _ZSH_PROMPT.match(line)
        if match and match.group("rest").strip():
            last_command_line = index

    if last_command_line is None:
        return "\n".join(lines[-200:])

    end = len(lines)
    for index in range(last_command_line + 1, len(lines)):
        if _ZSH_PROMPT.match(lines[index]):
            end = index
            break

    return "\n".join(lines[last_command_line + 1 : end])


def _run_osascript(script: str, action: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError("Murmur could not find osascript. This feature needs macOS.") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Timed out while trying to {action} after 5 seconds.") from error

    if result.returncode != 0:
        details = result.stderr.strip()
        if "-1743" in details or "not authorized" in details.lower():
            raise RuntimeError(
                "macOS blocked Murmur from controlling Terminal.app. Allow Terminal control in "
                "System Settings → Privacy & Security → Automation."
            )
        raise RuntimeError(f"Could not {action}: {details or 'AppleScript failed.'}")

    return result


def automation_permission_missing() -> bool:
    """Return true only when macOS specifically blocks Terminal automation."""
    try:
        _run_osascript(_AUTOMATION_CHECK_SCRIPT, "check Terminal.app automation access")
    except RuntimeError as error:
        return "macOS blocked Murmur from controlling Terminal.app." in str(error)
    return False


def tab_snapshot() -> TabSnapshot | None:
    """Peek at the front tab. Returns None when Terminal is closed or has no tab to watch."""
    answer = _run_osascript(_SNAPSHOT_SCRIPT, "check Terminal.app's front tab").stdout.strip()
    if answer in (_NOT_RUNNING, _NO_WINDOWS, _NO_TAB) or not answer:
        return None
    parts = answer.split("\t")
    if len(parts) != 3 or parts[1] not in ("true", "false"):
        raise RuntimeError(f"Could not understand Terminal's answer: {answer!r}")
    tty, busy, names = parts
    processes = tuple(name.strip() for name in names.split(",") if name.strip())
    return TabSnapshot(tty=tty, busy=busy == "true", processes=processes)


def _tab_history_script(tty: str) -> str:
    # The tty only ever looks like /dev/ttys003, but we still refuse anything odd
    # before putting it inside AppleScript.
    if not re.fullmatch(r"/dev/ttys\d+", tty):
        raise RuntimeError(f"Murmur will not read an unexpected Terminal tab name: {tty!r}")
    return f'''
tell application "Terminal"
    repeat with theWindow in windows
        repeat with theTab in tabs of theWindow
            if tty of theTab is "{tty}" then
                return history of theTab
            end if
        end repeat
    end repeat
    return "{_NO_TAB}"
end tell
'''


def get_tab_text(tty: str) -> str:
    """Return the scrollback of one particular tab, even if it is no longer in front."""
    result = _run_osascript(_tab_history_script(tty), "read that Terminal tab's history")
    if result.stdout.strip() == _NO_TAB:
        raise RuntimeError("That Terminal tab was closed before Murmur could read it.")
    return result.stdout


def get_terminal_text() -> str:
    """Return Terminal's selected-tab scrollback, or explain why it is unavailable."""
    running = _run_osascript(_RUNNING_SCRIPT, "check whether Terminal.app is running")
    running_state = running.stdout.strip()
    if running_state == "false":
        raise RuntimeError("Terminal.app is not running. Open Terminal.app and try again.")
    if running_state != "true":
        raise RuntimeError(
            "Could not determine whether Terminal.app is running; "
            f"osascript returned {running.stdout!r}."
        )

    result = _run_osascript(_HISTORY_SCRIPT, "read Terminal.app history")

    if result.stdout.strip() == _NO_WINDOWS:
        raise RuntimeError("Terminal.app has no windows open. Open a window and try again.")

    return result.stdout
