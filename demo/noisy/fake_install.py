#!/usr/bin/env python3
"""Print a fake package install with terminal noise for Murmur demos."""

import sys
import time

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def progress(package: str, size: str) -> None:
    for percent in (0, 18, 41, 67, 89, 100):
        filled = percent // 10
        bar = "█" * filled + "░" * (10 - filled)
        sys.stdout.write(
            f"\r{CYAN}Downloading {package} ({size}){RESET} "
            f"[{GREEN}{bar}{RESET}] {percent:3d}%"
        )
        sys.stdout.flush()
        time.sleep(0.09)
    sys.stdout.write("\n")


def main() -> None:
    print("$ uv pip install rich")
    print("Resolved 4 packages in 128ms")
    progress("rich", "312.4 KiB")
    progress("pygments", "1.2 MiB")
    print("Installing collected packages: mdurl, pygments, markdown-it-py, rich")
    print(f"{YELLOW}Package summary{RESET}")
    print("┌────────────────┬─────────┬──────────┐")
    print("│ Package        │ Version │ Size     │")
    print("├────────────────┼─────────┼──────────┤")
    print("│ mdurl          │ 0.1.2   │ 13.5 KiB │")
    print("│ pygments       │ 2.18.0  │ 1.2 MiB  │")
    print("│ markdown-it-py │ 3.0.0   │ 78.4 KiB │")
    print("│ rich           │ 13.9.4  │ 312 KiB  │")
    print("└────────────────┴─────────┴──────────┘")
    print(f"{GREEN}Installed 4 packages in 24ms{RESET}")
    print(f"{GREEN}Installation completed successfully.{RESET}")


if __name__ == "__main__":
    main()
