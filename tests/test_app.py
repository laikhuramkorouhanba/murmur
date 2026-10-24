from murmur import app
import rumps


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


def test_hotkey_uses_the_same_job_with_stop_on_repeat(monkeypatch) -> None:
    callbacks = []
    starts = []
    sentinel_listener = object()

    def fake_listener(callback):
        callbacks.append(callback)
        return sentinel_listener

    monkeypatch.setattr(app, "start_hotkey_listener", fake_listener)
    monkeypatch.setattr(
        app,
        "start_announcement",
        lambda **kwargs: starts.append(kwargs),
    )

    app.start_hotkey()
    callbacks[0]()

    assert app._HOTKEY_LISTENER is sentinel_listener
    assert starts == [{"stop_if_running": True}]


def test_hotkey_permission_problem_is_a_startup_warning(monkeypatch, capsys) -> None:
    def fail_to_listen(_callback):
        raise RuntimeError("Enable Input Monitoring and Accessibility.")

    monkeypatch.setattr(app, "start_hotkey_listener", fail_to_listen)

    app.start_hotkey()

    assert capsys.readouterr().err == (
        "Murmur startup warning: hotkeys ⌘⌥⇧M and Right Option twice are unavailable. "
        "Enable Input Monitoring and Accessibility.\n"
    )


def test_successful_hotkey_startup_prints_the_shortcut(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app, "start_hotkey_listener", lambda _callback: object())

    assert app.start_hotkey()

    assert capsys.readouterr().out == (
        "Murmur hotkey: ⌘⌥⇧M or Right Option twice starts announcements; "
        "press either again to stop speech.\n"
    )


def test_menu_title_displays_the_global_shortcut() -> None:
    assert app.READ_LAST_OUTPUT_MENU_TITLE == "Read last output (⌘⌥⇧M or Right Option twice)"


def test_menu_registers_shortcut_label_and_test_voice_item() -> None:
    menu_app = app.MurmurApp()
    for register in rumps.clicked.__dict__.get("*buttons", []):
        register(menu_app)

    assert app.READ_LAST_OUTPUT_MENU_TITLE in menu_app.menu
    assert "Test voice" in menu_app.menu
