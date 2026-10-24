"""Murmur's small macOS menu bar app."""

import rumps


class MurmurApp(rumps.App):
    """Show Murmur in the menu bar and provide a way to quit."""

    def __init__(self) -> None:
        super().__init__("Murmur", quit_button="Quit")


def main() -> None:
    MurmurApp().run()


if __name__ == "__main__":
    main()
