from murmur import app


def test_prints_cleaned_output_with_a_header_and_footer(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        app,
        "get_terminal_text",
        lambda: "shell % run\n\x1b[31mError: bad\x1b[0m   \nshell % ",
    )

    app.print_last_output()

    assert capsys.readouterr().out == (
        "----- Murmur: last command output -----\n"
        "Error: bad\n"
        "----- End of Murmur output -----\n"
    )


def test_prints_watcher_error_inside_the_output_markers(monkeypatch, capsys) -> None:
    def fail_to_read() -> str:
        raise RuntimeError("Terminal.app is not running.")

    monkeypatch.setattr(app, "get_terminal_text", fail_to_read)

    app.print_last_output()

    assert capsys.readouterr().out == (
        "----- Murmur: last command output -----\n"
        "Terminal.app is not running.\n"
        "----- End of Murmur output -----\n"
    )


def test_startup_warns_when_terminal_cannot_be_read(monkeypatch, capsys) -> None:
    def fail_to_read() -> str:
        raise RuntimeError("Terminal.app is not running.")

    monkeypatch.setattr(app, "get_terminal_text", fail_to_read)

    app.check_terminal_on_startup()

    assert capsys.readouterr().err == (
        "Murmur startup warning: Terminal.app is not running.\n"
    )
