"""The Murmur key layer: double tap, "Murmur", then one key that Terminal never sees."""

import pytest

from murmur import app, config, hotkey

from test_hotkey import FAKE_KEYBOARD, FakeClock, FakeKey, tap

SPACE, R, F, U, ESCAPE, LETTER_A = 49, 15, 3, 32, 53, 0


def make_layer():
    commands, switches = [], []
    clock = FakeClock()
    layer = hotkey.KeyLayer(commands.append, clock=clock, on_change=switches.append)
    return layer, commands, switches, clock


@pytest.mark.parametrize(
    ("code", "command"), [(SPACE, "repeat"), (R, "read"), (F, "fix"), (U, "undo"), (ESCAPE, "cancel")]
)
def test_each_layer_key_runs_its_command_and_is_swallowed(code, command) -> None:
    layer, commands, _switches, _clock = make_layer()
    layer.open()

    assert layer.key_down(code) is True  # swallowed: Terminal never sees it
    assert layer.key_up(code) is True  # and its key-up too
    assert commands == [command]


def test_only_one_key_per_double_tap() -> None:
    layer, commands, _switches, _clock = make_layer()
    layer.open()
    layer.key_down(R)
    layer.key_up(R)

    assert layer.key_down(R) is False  # the second R is typed normally
    assert commands == ["read"]


def test_any_other_key_closes_the_layer_and_goes_through() -> None:
    layer, commands, _switches, _clock = make_layer()
    layer.open()

    assert layer.key_down(LETTER_A) is False
    assert layer.key_down(R) is False  # closed now
    assert commands == []


def test_the_layer_closes_by_itself_after_about_three_seconds() -> None:
    layer, commands, _switches, clock = make_layer()
    layer.open()
    clock.now += config.KEY_LAYER_SECONDS + 0.1

    assert not layer.is_open()
    assert layer.key_down(R) is False
    assert commands == []


def test_a_held_key_keeps_being_swallowed_while_it_repeats() -> None:
    layer, _commands, _switches, _clock = make_layer()
    layer.open()
    layer.key_down(R)

    assert layer.key_down(R) is True  # macOS key repeat while held
    assert layer.key_up(R) is True
    assert layer.key_down(R) is False


def test_the_swallowing_tap_is_only_on_while_needed() -> None:
    layer, _commands, switches, _clock = make_layer()
    assert not layer.needs_swallowing()

    layer.open()
    assert switches[-1] is True
    layer.key_down(F)
    assert switches[-1] is True  # still on: the F key-up must be swallowed too
    layer.key_up(F)
    assert switches[-1] is False


def test_double_tap_opens_the_layer_instead_of_reading() -> None:
    opened, reads = [], []
    clock = FakeClock()
    watcher = hotkey.ShortcutWatcher(
        FAKE_KEYBOARD, lambda: reads.append(1), clock=clock, on_double_tap=lambda: opened.append(1)
    )

    tap(watcher, FakeKey.cmd_r, clock)
    clock.now += 0.1
    tap(watcher, FakeKey.cmd_r, clock)

    assert opened == [1] and reads == []


@pytest.mark.parametrize(
    ("command", "starter"),
    [("read", "start_read_full"), ("fix", "start_fix"), ("undo", "start_undo"), ("repeat", "start_repeat")],
)
def test_layer_commands_use_the_same_jobs_as_the_menu(monkeypatch, command, starter) -> None:
    calls = []
    monkeypatch.setattr(app, starter, lambda **kwargs: calls.append(kwargs) or True)

    app.run_layer_command(command)

    assert len(calls) == 1


def test_escape_stops_speech(monkeypatch) -> None:
    stops = []
    monkeypatch.setattr(app, "stop_talking", lambda: stops.append(1))

    app.run_layer_command("cancel")

    assert stops == [1]


def test_repeat_says_the_last_announcement_again(monkeypatch) -> None:
    spoken = []
    monkeypatch.setattr(app.voice, "speak", lambda text, **_kwargs: spoken.append(text) or "macOS say")
    monkeypatch.setattr(app.voice, "play_fail_sound", lambda: "sounddevice")
    history = "me % pytest\nE   assert 1 == 2\nt.py:3: AssertionError\nme % "
    app.announce_last_output(read_text=lambda: history)
    app._JOB_CANCELLED.clear()

    app._run_repeat_job()

    assert spoken == [spoken[0], spoken[0]]
    assert spoken[0].startswith("Failed.")
