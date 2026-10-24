"""Pretend to build for a while, then pass or fail, to try out auto-announce.

    python demo/slow_build.py 5 pass    -> soft sound
    python demo/slow_build.py 16 pass   -> one short "done" line
    python demo/slow_build.py 5 fail    -> full spoken announcement
"""

import sys
import time


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5
    outcome = sys.argv[2] if len(sys.argv) > 2 else "pass"
    print(f"Building the demo project for {seconds:g} seconds...")
    time.sleep(seconds)
    if outcome == "fail":
        print("src/checkout.py:12: SyntaxError: '(' was never closed")
        print("Build failed: 1 error.")
        return 1
    print("Build finished: 42 files compiled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
