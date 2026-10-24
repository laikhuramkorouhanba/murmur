"""Murmur's small macOS menu bar app."""

import sys

from murmur.cleaner import clean
from murmur.watcher import get_terminal_text, last_command_output
import rumps


def print_last_output() -> None:
    """Print the cleaned output of Terminal's most recent command."""
    print("----- Murmur: last command output -----")
    try:
        history = get_terminal_text()
        output = clean(last_command_output(history))
        print(output or "(No output found for the last command.)")
    except RuntimeError as error:
        print(error)
    print("----- End of Murmur output -----")


def check_terminal_on_startup() -> None:
    """Print a helpful warning if Terminal cannot be read yet."""
    try:
        get_terminal_text()
    except RuntimeError as error:
        print(f"Murmur startup warning: {error}", file=sys.stderr)


class MurmurApp(rumps.App):
    """Show Murmur in the menu bar and provide a way to quit."""

    def __init__(self) -> None:
        super().__init__("Murmur", quit_button="Quit")

    @rumps.clicked("Read last output")
    def read_last_output(self, _sender: object) -> None:
        print_last_output()


def main() -> None:
    check_terminal_on_startup()
    MurmurApp().run()


if __name__ == "__main__":
    main()
