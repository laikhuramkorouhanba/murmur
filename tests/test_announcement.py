import threading

import pytest

from murmur import app, personality, voice


@pytest.fixture(autouse=True)
def reset_job_state():
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_CANCELLED.clear()
    voice.reset_stop()
    personality.set_mode(personality.Mode.FRIENDLY)
    personality.record_result(False)
    yield
    with app._JOB_LOCK:
        app._JOB_ACTIVE = False
        app._JOB_CANCELLED.clear()
    voice.reset_stop()
    personality.set_mode(personality.Mode.FRIENDLY)
    personality.record_result(False)


def test_announcement_plays_the_pass_sound_then_reads_short_output(monkeypatch, capsys) -> None:
    history = "user@MacBook-Pro murmur % pytest -q\n. [100%]\n1 passed in 0.01s\nuser@MacBook-Pro murmur % "
    heard = []
    monkeypatch.setattr(app, "get_terminal_text", lambda: history)
    monkeypatch.setattr(app.voice, "play_pass_sound", lambda: heard.append("pass sound") or "sounddevice")
    monkeypatch.setattr(app.voice, "speak", lambda text, **_kwargs: heard.append(text) or "macOS say")

    app.announce_last_output()

    assert heard == ["pass sound", ". [100%]\n1 passed in 0.01s"]
    assert capsys.readouterr().out == (
        "----- Murmur: cleaned terminal output -----\n"
        ". [100%]\n"
        "1 passed in 0.01s\n"
        "----- Murmur: spoken text -----\n"
        "(short output, read word for word)\n"
        "Voice engine: macOS say\n"
        "----- End of Murmur announcement -----\n"
    )


def test_a_failure_plays_the_fail_sound_then_speaks_brief_words(monkeypatch) -> None:
    personality.set_mode(personality.Mode.BRIEF)
    history = (
        "me@mac shop % pytest -q\n"
        "E       AssertionError: assert Decimal('18.40') == Decimal('21.60')\n"
        "shop_demo_check.py:9: AssertionError\n"
        "1 failed in 0.10s\n"
        "me@mac shop % "
    )
    heard = []
    monkeypatch.setattr(app, "summarise", lambda text: pytest.fail("Brief needs no summary here"))
    monkeypatch.setattr(app.voice, "play_fail_sound", lambda: heard.append("fail sound") or "sounddevice")
    monkeypatch.setattr(app.voice, "speak", lambda text, **_kwargs: heard.append(text) or "macOS say")

    app.announce_last_output(read_text=lambda: history)

    assert heard == ["fail sound", "Failed. shop demo check dot py, line 9. Expected 21.60, got 18.40."]


def test_voiceover_gets_the_real_file_name_for_braille(monkeypatch) -> None:
    personality.set_mode(personality.Mode.BRIEF)
    monkeypatch.setattr(app.voice, "engine", lambda: "voiceover")
    spoken = []
    monkeypatch.setattr(app.voice, "play_fail_sound", lambda: "sounddevice")
    monkeypatch.setattr(app.voice, "speak", lambda text, **_kwargs: spoken.append(text) or "VoiceOver")
    history = "me % python app.py\nTraceback (most recent call last):\n  File \"/srv/app_main.py\", line 3, in <module>\nNameError: name 'x' is not defined\nme % "

    app.announce_last_output(read_text=lambda: history)

    assert spoken == ["Failed. app_main.py, line 3. NameError: name 'x' is not defined"]


def test_long_success_says_how_many_lines(monkeypatch) -> None:
    personality.set_mode(personality.Mode.BRIEF)
    lines = "\n".join(f"step {n} ok" for n in range(25))
    spoken = []
    monkeypatch.setattr(app.voice, "play_pass_sound", lambda: "sounddevice")
    monkeypatch.setattr(app.voice, "speak", lambda text, **_kwargs: spoken.append(text) or "macOS say")

    app.announce_last_output(read_text=lambda: f"me % make\n{lines}\nme % ")

    assert spoken == ["Done, 25 lines."]


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
    personality.set_mode(personality.Mode.BRIEF)
    spoken = []

    def fake_speak(text, *, mode):
        spoken.append((text, mode))
        return "OpenAI"

    monkeypatch.setattr(app.voice, "speak", fake_speak)

    app._run_test_voice_job()

    assert spoken == [("Murmur voice check.", personality.Mode.BRIEF)]
    assert capsys.readouterr().out == (
        "----- Murmur: test voice -----\n"
        "Murmur voice check.\n"
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
