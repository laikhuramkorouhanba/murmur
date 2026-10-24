from pathlib import Path

from murmur.watcher import last_command_output


SAMPLES = Path(__file__).parent / "samples"


def test_keeps_only_the_latest_zsh_command_output() -> None:
    history = (SAMPLES / "zsh_history.txt").read_text(encoding="utf-8")

    output = last_command_output(history)

    assert output.startswith("F                                                                        [100%]")
    assert "laikhuram@MacBook-Pro ~ % pwd" not in output
    assert "demo/failing_check.py:6: AssertionError" in output


def test_skips_empty_prompt_and_preserves_carriage_returns() -> None:
    history = "murmur % python progress.py\n0%\r100%\nDone\nmurmur % "

    assert last_command_output(history) == "0%\r100%\nDone"


def test_uses_last_200_lines_when_there_is_no_prompt() -> None:
    history = "\n".join(f"line {number}" for number in range(250))

    assert last_command_output(history).splitlines() == [
        f"line {number}" for number in range(50, 250)
    ]
