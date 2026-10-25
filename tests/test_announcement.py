import threading

import pytest

from murmur import app, personality, voice


@pytest.fixture(autouse=True)
def reset_job_state():
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_CANCELLED.clear()
    voice.reset_stop()
    personality.set_mode(personality.Mode.SMOOTH)
    personality.record_result(False)
    yield
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_CANCELLED.clear()
    voice.reset_stop()
    personality.set_mode(personality.Mode.SMOOTH)
    personality.record_result(False)


def test_announcement_prints_cleaned_output_and_plays_success_cue(monkeypatch, capsys) -> None:
    history = "user@MacBook-Pro murmur % pytest -q\n. [100%]\n1 passed in 0.01s\nuser@MacBook-Pro murmur % "
    cues = []
    monkeypatch.setattr(app, "get_terminal_text", lambda: history)
    monkeypatch.setattr(
        app.voice,
        "play_soft_sound",
        lambda: (cues.append(True) or "sounddevice"),
    )

    app.announce_last_output()

    assert cues == [True]
    assert capsys.readouterr().out == (
        "----- Murmur: cleaned terminal output -----\n"
        ". [100%]\n"
        "1 passed in 0.01s\n"
        "----- Murmur: spoken text -----\n"
        "(none; playing the soft success sound)\n"
        "Voice engine: sounddevice\n"
        "----- End of Murmur announcement -----\n"
    )


def test_announcement_speaks_short_output_exactly(monkeypatch, capsys) -> None:
    history = "user@MacBook-Pro murmur % pwd\n/Users/laikhuram/murmur\nuser@MacBook-Pro murmur % "
    spoken = []
    monkeypatch.setattr(app, "get_terminal_text", lambda: history)
    monkeypatch.setattr(
        app.voice,
        "speak",
        lambda text, **_kwargs: (spoken.append(text) or "OpenAI"),
    )

    app.announce_last_output()

    assert spoken == ["/Users/laikhuram/murmur"]
    output = capsys.readouterr().out
    assert "/Users/laikhuram/murmur\n----- Murmur: spoken text -----" in output
    assert "Voice engine: OpenAI" in output


def test_announcement_speaks_an_assertion_failure_without_a_summary_request(monkeypatch, capsys) -> None:
    history = (
        "user@MacBook-Pro murmur % pytest -q\n"
        "FAILED demo/failing_check.py::test_total_includes_the_tip\n"
        "E       assert 18.0 == 19.8\n"
        "demo/failing_check.py:6: AssertionError\n"
        "user@MacBook-Pro murmur % "
    )
    summaries = []
    spoken = []
    monkeypatch.setattr(app, "get_terminal_text", lambda: history)
    monkeypatch.setattr(app, "summarise", lambda text: (summaries.append(text) or "The test failed."))
    monkeypatch.setattr(
        app.voice,
        "speak",
        lambda text, **_kwargs: (spoken.append(text) or "OpenAI"),
    )

    app.announce_last_output()

    assert summaries == []  # "it expected X but got Y" needs no summary request
    assert (
        "One test failed in failing check dot py, line 6: "
        "it expected 19.8 but got 18." in spoken[0]
    )
    assert any(spoken[0].endswith(closer) for closer in personality._PLAYFUL_CLOSERS)
    assert any(spoken[0].startswith(opener) for opener in personality._PLAYFUL_OPENERS)
    output = capsys.readouterr().out
    assert spoken[0] in output
    assert "demo/failing_check.py:6: AssertionError" in output
    assert "demo/failing_check.py:6: AssertionError" not in spoken[0]
    assert "Voice engine: OpenAI" in output


def test_cancellation_during_summary_prevents_speech(monkeypatch, capsys) -> None:
    history = "user@MacBook-Pro murmur % pytest -q\nFAILED test.py\nuser@MacBook-Pro murmur % "
    cancelled = threading.Event()
    spoken = []

    def summarise_and_cancel(_text: str) -> str:
        cancelled.set()
        return "The command failed."

    monkeypatch.setattr(app, "get_terminal_text", lambda: history)
    monkeypatch.setattr(app, "summarise", summarise_and_cancel)
    monkeypatch.setattr(app.voice, "speak", spoken.append)

    app.announce_last_output(cancelled)

    assert spoken == []
    assert "The command failed." not in capsys.readouterr().out


def test_only_one_background_job_starts_at_a_time(monkeypatch) -> None:
    threads = []

    class FakeThread:
        def __init__(self, *, target, **kwargs):
            self.target = target
            self.arguments = kwargs
            threads.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(app.threading, "Thread", FakeThread)
    monkeypatch.setattr(app, "announce_last_output", lambda *_args: None)

    assert app.start_announcement()
    assert not app.start_announcement()
    assert len(threads) == 1
    assert threads[0].arguments["daemon"] is True
    assert threads[0].started

    threads[0].target()
    assert app._JOB_ACTIVE is False


def test_menu_action_starts_the_background_job(monkeypatch) -> None:
    starts = []
    monkeypatch.setattr(app, "start_announcement", lambda: starts.append(True))

    app.MurmurApp.read_last_output(None, None)

    assert starts == [True]


def test_hotkey_style_repeat_can_cancel_the_current_job(monkeypatch) -> None:
    stopped = []
    app._JOB_ACTIVE = True
    monkeypatch.setattr(app.voice, "stop_speech", lambda: stopped.append(True))

    assert not app.start_announcement(stop_if_running=True)

    assert app._JOB_CANCELLED.is_set()
    assert stopped == [True]


def test_test_voice_uses_the_selected_mode_and_prints_engine(monkeypatch, capsys) -> None:
    personality.set_mode(personality.Mode.FOCUSED)
    spoken = []

    def fake_speak(text, *, mode):
        spoken.append((text, mode))
        return "OpenAI"

    monkeypatch.setattr(app.voice, "speak", fake_speak)

    app._run_test_voice_job()

    assert spoken == [("Murmur voice check is ready.", personality.Mode.FOCUSED)]
    assert capsys.readouterr().out == (
        "----- Murmur: test voice -----\n"
        "Murmur voice check is ready.\n"
        "Voice engine: OpenAI\n"
        "----- End of Murmur voice test -----\n"
    )


def test_test_voice_menu_action_starts_its_background_job(monkeypatch) -> None:
    starts = []
    monkeypatch.setattr(app, "start_test_voice", lambda: starts.append(True))

    app.MurmurApp.test_voice(None, None)

    assert starts == [True]


def test_assertion_failures_skip_the_summary_request(monkeypatch) -> None:
    # Found by the self-test: the summary was requested (1-2 s) and then thrown away.
    from murmur import app

    monkeypatch.setattr(app, "summarise", lambda text: pytest.fail("no summary request needed"))
    spoken = []
    monkeypatch.setattr(app.voice, "speak", lambda text, **kwargs: spoken.append(text) or "OpenAI")
    history = (
        "shop % pytest -q\n"
        "E       AssertionError: assert Decimal('18.40') == Decimal('21.60')\n"
        "shop_demo_check.py:9: AssertionError\n"
        "1 failed in 0.10s\n"
        "shop % "
    )

    app.announce_last_output(read_text=lambda: history)

    assert "it expected 21.60 but got 18.40" in spoken[0]


def test_other_errors_are_still_summarised(monkeypatch) -> None:
    frames = "".join(f'  File "/app/step{n}.py", line {n}, in step{n}\n    step{n + 1}()\n' for n in range(20))
    output = f"Traceback (most recent call last):\n{frames}ModuleNotFoundError: No module named 'requests'"
    history = f"shop % python app.py\n{output}\nshop % "
    summaries = []
    monkeypatch.setattr(app, "summarise", lambda text: summaries.append(text) or "A module is missing.")
    monkeypatch.setattr(app.voice, "speak", lambda text, **kwargs: "OpenAI")

    app.announce_last_output(read_text=lambda: history)

    assert len(summaries) == 1 and "No module named 'requests'" in summaries[0]
