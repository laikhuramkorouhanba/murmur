"""The stage demo: the demo project, Reset demo project, and (later in this file) Demo mode.

The demo project is a tiny shop checkout at ~/murmur-fixme-demo with one planted
bug. demo/make_fixme_project.sh builds it, and refuses to delete any folder that
it didn't make itself (it looks for a .murmur-demo marker).
"""

import subprocess
from pathlib import Path

from murmur import fixer

REPO = Path(__file__).resolve().parents[2]
DEMO_DIR = Path.home() / "murmur-fixme-demo"
BUILDER = REPO / "demo" / "make_fixme_project.sh"
RESET_LINE = "Demo project reset. The bug is back."


class DemoProblem(Exception):
    """The demo can't go ahead. The message is written to be spoken."""


def reset_demo_project() -> str:
    """Rebuild the demo project with its bug, and forget any old failure or fix. Returns the line to say."""
    if not BUILDER.exists():
        raise DemoProblem("I can't find the demo builder. Reset demo project needs Murmur's source folder.")
    result = subprocess.run([str(BUILDER)], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0:
        # The builder explains itself, for example when the folder isn't ours to delete.
        reason = (result.stderr.strip() or result.stdout.strip() or "it stopped").splitlines()[0]
        raise DemoProblem(f"The demo project wasn't reset: {reason}")
    fixer.forget_failure()
    fixer.remember_fix(None)  # that fix belonged to the old copy
    fixer.DIFF_PATH.unlink(missing_ok=True)
    return RESET_LINE
