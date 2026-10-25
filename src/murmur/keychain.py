"""Keep the OpenAI API key in the macOS Keychain, so the app never needs a .env file.

We use `security`, the Keychain tool built into every Mac, so there's nothing
extra to install. The key is never put on a command line (where other programs
could see it in the process list): it is typed into `security`'s own prompt
through stdin instead.
"""

import re
import subprocess

SERVICE = "Murmur"
ACCOUNT = "OPENAI_API_KEY"
LABEL = "Murmur OpenAI API key"

# OpenAI keys only use these characters. Checking this also means the key can't
# break out of the `security` command we type it into.
_KEY_SHAPE = re.compile(r"sk-[A-Za-z0-9_\-]{10,}")


def looks_like_a_key(key: str) -> bool:
    return bool(_KEY_SHAPE.fullmatch(key.strip()))


def read_key() -> str | None:
    """The saved key, or None if there isn't one (or the Keychain can't be read)."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    key = result.stdout.strip()
    return key if result.returncode == 0 and key else None


def save_key(key: str) -> None:
    """Save (or replace) the key. Raises ValueError with a plain reason if it can't."""
    key = key.strip()
    if not looks_like_a_key(key):
        raise ValueError("That doesn't look like an OpenAI API key. They start with sk-.")
    command = f'add-generic-password -U -s {SERVICE} -a {ACCOUNT} -l "{LABEL}" -w {key}\n'
    try:
        subprocess.run(
            ["security", "-i"], input=command, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"The Keychain didn't answer ({type(error).__name__}).") from None
    # `security -i` doesn't report failures in its exit code, so read the key back to be sure.
    if read_key() != key:
        raise ValueError("The Keychain didn't keep the key. Is the login keychain locked?")


def delete_key() -> None:
    subprocess.run(
        ["security", "delete-generic-password", "-s", SERVICE, "-a", ACCOUNT],
        capture_output=True,
        timeout=10,
        check=False,
    )
