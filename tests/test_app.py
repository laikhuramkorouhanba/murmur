import pytest

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

    def fake_listener(callback, on_fix=None, on_read=None):
        callbacks.append(callback)
        assert on_fix is app.start_fix  # ⌘⌥⇧F goes straight to Fix it
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
    def fail_to_listen(_callback, on_fix=None, on_read=None):
        raise RuntimeError("Enable Input Monitoring and Accessibility.")

    monkeypatch.setattr(app, "start_hotkey_listener", fail_to_listen)

    app.start_hotkey()

    assert capsys.readouterr().err == (
        "Murmur startup warning: hotkeys ⌘⌥⇧M, Right Command twice, ⌘⌥⇧F and ⌘⌥⇧R are unavailable. "
        "Enable Input Monitoring and Accessibility.\n"
    )


def test_successful_hotkey_startup_prints_the_shortcut(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app, "start_hotkey_listener", lambda _callback, on_fix=None, on_read=None: object())

    assert app.start_hotkey()

    assert capsys.readouterr().out == (
        "Murmur hotkey: ⌘⌥⇧M or Right Command twice starts announcements; "
        "press either again to stop speech. ⌘⌥⇧F asks Codex to fix the last error. "
        "⌘⌥⇧R reads the full output word for word.\n"
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


# --- the API key box -------------------------------------------------------


class FakeKeyWindow:
    """Stands in for rumps.Window: remembers how it was set up and answers like a person would."""

    def __init__(self, clicked: int, text: str):
        self.clicked, self.text = clicked, text
        self.options = {}

    def __call__(self, **options):
        self.options = options
        return self

    def run(self):
        return self


def test_the_key_box_hides_the_key_and_saves_it(monkeypatch) -> None:
    saved = []
    monkeypatch.setattr(app.keychain, "save_key", saved.append)
    monkeypatch.setattr(app.voice, "warm_up", lambda: None)
    monkeypatch.setattr(app.config, "OPENAI_API_KEY", None)
    window = FakeKeyWindow(1, "  sk-proj-abcdefghijklmnop  ")

    assert app.ask_for_api_key(make_window=window)

    assert window.options["secure"] is True
    assert saved == ["sk-proj-abcdefghijklmnop"]
    assert app.config.OPENAI_API_KEY == "sk-proj-abcdefghijklmnop"
    assert app.config.API_KEY_SOURCE == "the Keychain"


def test_cancelling_the_key_box_saves_nothing(monkeypatch) -> None:
    monkeypatch.setattr(app.keychain, "save_key", lambda key: pytest.fail("nothing should be saved"))
    window = FakeKeyWindow(0, "sk-proj-abcdefghijklmnop")

    assert not app.ask_for_api_key(first_run=True, make_window=window)
    assert window.options["cancel"] == "Use the Mac voice"


def test_a_bad_key_is_explained_not_saved(monkeypatch) -> None:
    alerts = []
    monkeypatch.setattr(app.rumps, "alert", lambda **kwargs: alerts.append(kwargs))
    monkeypatch.setattr(app.config, "OPENAI_API_KEY", None)

    assert not app.ask_for_api_key(make_window=FakeKeyWindow(1, "hello"))

    assert "start with sk-" in alerts[0]["message"]
    assert app.config.OPENAI_API_KEY is None


def test_first_run_asks_once_and_only_without_a_key(monkeypatch) -> None:
    asked = []
    monkeypatch.setattr(app, "ask_for_api_key", lambda **kwargs: asked.append(kwargs) or False)
    monkeypatch.setattr(app.config, "OPENAI_API_KEY", None)

    app.ask_for_api_key_if_first_run()
    app.ask_for_api_key_if_first_run()

    assert asked == [{"first_run": True}]


def test_no_first_run_box_when_a_key_exists(monkeypatch) -> None:
    monkeypatch.setattr(app, "ask_for_api_key", lambda **kwargs: pytest.fail("should not ask"))
    monkeypatch.setattr(app.config, "OPENAI_API_KEY", "sk-already-here")

    assert not app.ask_for_api_key_if_first_run()


def test_key_status_names_where_the_key_came_from_but_never_the_key(monkeypatch) -> None:
    monkeypatch.setattr(app.config, "OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setattr(app.config, "API_KEY_SOURCE", "the Keychain")

    status = app.api_key_status()

    assert status == "Murmur: using the OpenAI API key from the Keychain."
    assert "sk-" not in status
