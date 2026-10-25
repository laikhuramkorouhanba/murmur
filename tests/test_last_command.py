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


def test_percent_sign_in_a_traceback_is_not_a_prompt() -> None:
    history = (SAMPLES / "zsh_percent_in_traceback.txt").read_text(encoding="utf-8")

    output = last_command_output(history)

    # The whole error must survive, including the file name and line number.
    assert output.startswith("F ")
    assert 'msg = "%s failed %s" % name' in output
    assert "tests/test_greet.py:4:" in output
    assert "app.py:2: TypeError" in output


def test_percent_sign_in_a_progress_line_is_not_a_prompt() -> None:
    history = (SAMPLES / "zsh_percent_in_progress.txt").read_text(encoding="utf-8")

    assert last_command_output(history) == (
        "Downloading requests 100 % done\n"
        "Installed 5 packages in 12ms\n"
        " + requests==2.32.3"
    )


def test_prompt_with_spaces_in_the_folder_name() -> None:
    history = "ada@mac My Project % ls\nnotes.txt\nada@mac My Project % "

    assert last_command_output(history) == "notes.txt"


# Regression: with a Python virtual env active, the prompt starts with "(murmur) ".
# Those prompts weren't recognised, so Murmur read an older command's output and
# Fix it then said "Nothing to fix" (found in the M5 live test).
def test_prompt_with_a_virtual_env_name_in_front() -> None:
    from murmur.watcher import last_command_line

    history = (SAMPLES / "zsh_venv_prompt.txt").read_text(encoding="utf-8")

    output = last_command_output(history)

    assert last_command_line(history) == "cd ~/murmur-fixme-demo && uv run --project ~/murmur pytest -q shop_demo_check.py"
    assert output.startswith("F    ")
    assert "Created the Fix it demo" not in output
    assert output.rstrip().endswith("1 failed in 0.10s")


def test_conda_and_nested_env_names_are_prompts_too() -> None:
    from murmur.watcher import last_command_line

    for prompt in ("(base) ada@macbook shop %", "(base) (murmur) ada@macbook shop %", "(.venv) shop %"):
        assert last_command_line(f"{prompt} pytest -q\nok\n{prompt} ") == "pytest -q"


def test_brackets_in_normal_output_are_not_a_prompt() -> None:
    history = "shop % make\n(2/3) compiling 50 % done\n(3/3) linking\nbuild ok\nshop % "

    assert last_command_output(history) == "(2/3) compiling 50 % done\n(3/3) linking\nbuild ok"
