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
        "Murmur startup warning: hotkeys ⌘⌥⇧M and Right Command twice are unavailable. "
        "Enable Input Monitoring and Accessibility.\n"
    )


def test_successful_hotkey_startup_prints_the_shortcut(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app, "start_hotkey_listener", lambda _callback: object())

    assert app.start_hotkey()

    assert capsys.readouterr().out == (
        "Murmur hotkey: ⌘⌥⇧M or Right Command twice starts announcements; "
        "press either again to stop speech.\n"
    )


def test_menu_title_displays_the_global_shortcut() -> None:
    assert app.read_last_output_title() == "Read last output (⌘⌥⇧M or Right Command twice)"


def test_menu_registers_shortcut_label_and_test_voice_item() -> None:
    menu_app = app.MurmurApp()
    for register in rumps.clicked.__dict__.get("*buttons", []):
        register(menu_app)

    assert menu_app.read_item.title == app.read_last_output_title()
    assert "Test voice" in menu_app.menu


def test_double_tap_menu_saves_the_choice_and_updates_the_label() -> None:
    menu_app = app.MurmurApp()
    assert menu_app.tap_items["right_command"].state == 1

    menu_app.choose_double_tap("right_shift")

    assert app.settings.get("double_tap_key") == "right_shift"
    assert menu_app.tap_items["right_shift"].state == 1
    assert menu_app.tap_items["right_command"].state == 0
    assert menu_app.read_item.title == "Read last output (⌘⌥⇧M or Right Shift twice)"


def test_voice_menu_saves_the_choice_and_marks_it() -> None:
    menu_app = app.MurmurApp()
    assert menu_app.voice_items["marin"].state == 1

    menu_app.choose_voice("cedar")

    assert app.settings.get("voice") == "cedar"
    assert app.voice.current_voice() == "cedar"
    assert menu_app.voice_items["cedar"].state == 1
    assert menu_app.voice_items["marin"].state == 0


def spoken(item) -> str:
    """What VoiceOver will say for this menu item."""
    return str(item._menuitem.accessibilityLabel())


def test_every_menu_item_has_plain_spoken_words() -> None:
    menu_app = app.MurmurApp()

    assert spoken(menu_app.read_item) == (
        "Read last output. Shortcut: Command Option Shift M, or Right Command twice."
    )
    assert spoken(menu_app.personality_menu) == "Personality, currently Smooth"
    assert spoken(menu_app.voice_menu) == "Voice, currently Marin"
    assert spoken(menu_app.tap_menu) == "Double-tap key, currently Right Command"
    assert spoken(menu_app.test_voice_item) == "Test voice: speak a sample sentence"
    assert spoken(menu_app.welcome_item) == (
        "Replay welcome: hear the shortcuts and missing permission help again"
    )
    assert spoken(menu_app.tap_items["right_shift"]) == "Right Shift: tap it twice to read the last output"
    assert menu_app.quit_button.title == "Quit Murmur"


def test_submenu_titles_show_the_current_choice_and_follow_changes() -> None:
    menu_app = app.MurmurApp()
    assert menu_app.voice_menu.title == "Voice: Marin"

    menu_app.choose_voice("cedar")
    menu_app.choose_double_tap("right_option")
    menu_app.choose_focused(None)

    assert menu_app.voice_menu.title == "Voice: Cedar"
    assert menu_app.tap_menu.title == "Double-tap key: Right Option"
    assert menu_app.personality_menu.title == "Personality: Focused"
    assert spoken(menu_app.read_item).endswith("or Right Option twice.")
    app.personality.set_mode(app.personality.Mode.SMOOTH)
