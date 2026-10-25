"""Record one real Fix it run, so Demo mode can replay it later with no Codex or network.

    uv run python demo/record_demo.py

It rebuilds the demo project, runs its failing test, asks the real Codex to fix
it (this uses Codex quota once), and saves what happened to demo/recorded/:
  demo.json  the failing command and output, Codex's answer, and how long it took
  fix.diff   Codex's exact change
Then it resets the demo project, so the bug is back.
"""

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from murmur import demo, fixer, voice
from murmur.cleaner import clean

RECORDED = Path(__file__).resolve().parent / "recorded"
COMMAND = "uv run pytest -q shop_demo_check.py"


def main() -> int:
    voice.set_muted(True)
    scratch = Path(tempfile.mkdtemp(prefix="murmur-record-"))
    fixer.DIFF_PATH = scratch / "fix.diff"
    fixer.LOG_PATH = scratch / "codex.log"

    print(demo.reset_demo_project())
    test = subprocess.run(
        ["uv", "run", "--project", str(demo.REPO), "pytest", "-q", "shop_demo_check.py"],
        cwd=demo.DEMO_DIR, capture_output=True, text=True, check=False,
    )
    output = test.stdout.replace(str(Path.home()), "~").rstrip()  # no home folder names in the recording
    if test.returncode == 0:
        print("The demo test passed, so there is nothing to record.")
        return 1

    answers = []

    def recording_codex(*args, **kwargs):
        started = time.monotonic()
        answer = fixer.run_codex(*args, **kwargs)
        answers.append((answer, time.monotonic() - started))
        return answer

    fixer.remember_failure(fixer.LastFailure(None, str(demo.DEMO_DIR), COMMAND, clean(output)))
    said = []
    fix = fixer.fix_last_failure(threading.Event(), say=said.append, tick=lambda: None, runner=recording_codex)
    if fix is None or not answers:
        print("Codex didn't make a fix:", said)
        return 1

    answer, seconds = answers[0]
    RECORDED.mkdir(exist_ok=True)
    (RECORDED / "fix.diff").write_text(fixer.DIFF_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    record = {
        "command": COMMAND,
        "output": output,
        "codex": {"fixed": answer.fixed, "summary": answer.summary, "verified": answer.verified},
        "codex_seconds": round(seconds, 1),
        "files": fix.files,
        "recorded": time.strftime("%Y-%m-%d"),
    }
    (RECORDED / "demo.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded: Codex took {seconds:.1f} s and changed {', '.join(fix.files)}.")
    print(f"Codex said: {answer.summary}")
    print("Murmur said:", *said, sep="\n  ")
    print(demo.reset_demo_project())
    return 0


if __name__ == "__main__":
    sys.exit(main())
