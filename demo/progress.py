"""Print a short progress bar with redraws and ANSI colours."""

import sys


def main() -> None:
    sys.stdout.write("\033[33mDownloading: 0%\033[0m\r")
    sys.stdout.flush()
    sys.stdout.write("\033[32mDownloading: 50%\033[0m\r")
    sys.stdout.flush()
    sys.stdout.write("\033[32mDownloading: 100%\033[0m\n")
    sys.stdout.write("Finished installing package.\n")


if __name__ == "__main__":
    main()
