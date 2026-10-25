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

    def fake_listener(callback, on_fix=None, on_read=None, **_more):
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
    def fail_to_listen(_callback, on_fix=None, on_read=None, **_more):
        raise RuntimeError("Enable Input Monitoring and Accessibility.")

    monkeypatch.setattr(app, "start_hotkey_listener", fail_to_listen)

    app.start_hotkey()

    assert capsys.readouterr().err == (
        "Murmur startup warning: hotkeys ⌘⌥⇧M, Right Command twice, ⌘⌥⇧F and ⌘⌥⇧R are unavailable. "
        "Enable Input Monitoring and Accessibility.\n"
    )


def test_successful_hotkey_startup_prints_the_shortcut(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app, "start_hotkey_listener", lambda _callback, on_fix=None, on_read=None, **_more: object())

    assert app.start_hotkey()

    assert capsys.readouterr().out == (
        "Murmur keys: double tap Right Command, then Space repeats, R reads all, F fixes, U undoes, "
        "Escape cancels. Backups: ⌘⌥⇧M reads the last output, ⌘⌥⇧F fixes the last error with Codex, "
        "⌘⌥⇧R reads the full output. Control stops speech.\n"
    )


def test_menu_title_displays_the_global_shortcut() -> None:
    assert app.read_last_output_title() == "Read last result (⌘⌥⇧M)"


def test_the_menu_is_in_the_new_order() -> None:
    menu_app = app.MurmurApp()

    titles = [str(item.title()) for item in menu_app._menu._menu.itemArray()]
    assert titles == [
        "Read last result (⌘⌥⇧M)",
        "Read everything (⌘⌥⇧R)",
        "Fix last error (⌘⌥⇧F)",
        "Undo last fix",
        "",
        "Voice: Mac voice",
        "Speed",
        "Auto-announce",
        "Show captions",
        "",
        "Demo",
        "More",
    ]  # rumps adds "Quit Murmur" at the end when the app starts
    assert list(menu_app.demo_menu.keys()) == ["Demo mode", "Reset demo project"]
    assert list(menu_app.more_menu.keys()) == ["Set OpenAI API key…", "Show log"]
    for gone in ("Test voice", "Style", "Double-tap key", "Replay welcome"):
        assert gone not in menu_app.menu


def test_voice_menu_offers_the_mac_voice_and_marin() -> None:
    menu_app = app.MurmurApp()
    assert list(menu_app.voice_items) == ["mac", "natural"]
    assert menu_app.voice_items["mac"].state == 1

    menu_app.choose_voice("natural")

    assert app.settings.get("engine") == "natural"
    assert menu_app.voice_items["natural"].state == 1 and menu_app.voice_items["mac"].state == 0
    assert menu_app.voice_menu.title == "Voice: Natural voice"
    assert spoken(menu_app.voice_menu) == "Voice, currently Natural voice"


def test_the_speed_slider_saves_and_says_a_sample_when_settled(monkeypatch) -> None:
    samples = []
    monkeypatch.setattr(app, "start_speed_sample", lambda: samples.append(app.voice.speed()))
    menu_app = app.MurmurApp()
    assert menu_app.speed_item._menuitem.view() is not None  # a real slider sits in the menu
    assert abs(menu_app.speed_slider.slider.doubleValue() - 0.5) < 1e-9

    menu_app.speed_changed(0.8)
    menu_app.speed_settled(0.8)

    assert app.settings.get("speed") == 0.8
    assert samples == [0.8]


def test_the_speed_slider_is_a_named_slider_voiceover_can_adjust(monkeypatch) -> None:
    from murmur.slider import SpeedSlider

    changes, settled, later = [], [], []
    slider = SpeedSlider(0.5, changes.append, settled.append, later=lambda _s, work: later.append(work), mouse_down=lambda: False)
    cell = slider.slider.cell()

    assert cell.accessibilityRole() == "AXSlider"
    assert cell.accessibilityLabel() == "Speed"
    assert cell.accessibilityValueDescription() == "50 percent"
    cell.accessibilityPerformIncrement()  # what VoiceOver does to make it go up

    assert changes and changes[-1] > 0.5
    assert cell.accessibilityValueDescription() == f"{round(changes[-1] * 100)} percent"
    for work in later:
        work()
    assert settled == [changes[-1]]  # one sample after the change settles


def test_dragging_waits_until_you_let_go() -> None:
    from murmur.slider import SpeedSlider

    pressed = {"down": True}
    settled, later = [], []
    slider = SpeedSlider(0.5, lambda _p: None, settled.append, later=lambda _s, work: later.append(work), mouse_down=lambda: pressed["down"])

    slider._changed(0.6)
    slider._changed(0.7)
    assert later == []  # still dragging
    pressed["down"] = False
    slider._changed(0.75)  # let go
    for work in later:
        work()

    assert settled == [0.75]


def test_arrow_keys_step_the_speed_and_say_so(monkeypatch) -> None:
    said = []
    monkeypatch.setattr(app, "_say_soon", said.append)

    app.voice.set_speed(0.5)
    assert app.change_speed(+1) == "Faster."
    assert app.voice.speed() == 0.6
    assert app.change_speed(-1) == "Slower."
    app.voice.set_speed(1.0)
    assert app.change_speed(+1) == "Fastest."
    assert said == ["Faster.", "Slower.", "Fastest."]


def spoken(item) -> str:
    """What VoiceOver will say for this menu item."""
    return str(item._menuitem.accessibilityLabel())


def test_every_menu_item_has_plain_spoken_words() -> None:
    menu_app = app.MurmurApp()

    assert spoken(menu_app.read_item) == (
        "Read last result. Shortcut: Command Option Shift M, or double tap Right Command then Space."
    )
    assert spoken(menu_app.read_full_item).startswith("Read everything, the whole output word for word.")
    assert spoken(menu_app.voice_menu) == "Voice, currently Mac voice"
    assert spoken(menu_app.voice_items["mac"]) == "Mac voice: fast, works offline"
    assert spoken(menu_app.voice_items["natural"]) == "Natural voice: Marin from OpenAI, needs the internet"
    assert spoken(menu_app.auto_item).endswith("On")
    assert spoken(menu_app.demo_menu) == "Demo: replay and reset the stage demo"
    assert spoken(menu_app.more_menu) == "More: API key and log"
    assert spoken(menu_app.key_item) == "Set OpenAI API key: saved in your Mac's Keychain"
    assert spoken(menu_app.log_item) == "Show log: everything Murmur printed, in TextEdit"
    assert menu_app.quit_button.title == "Quit Murmur"


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
