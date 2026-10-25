"""The self-test's own logic. The real run opens a Terminal window, so it isn't run here."""

import io
import threading

from murmur import selftest, voice


def test_default_is_muted_and_options_reach_the_run(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(selftest, "run", lambda **kwargs: calls.append(kwargs) or 0)

    selftest.main([])
    selftest.main(["--speak", "--no-codex"])

    assert calls == [
        {"no_codex": False, "muted": True, "demo_mode": False},
        {"no_codex": True, "muted": False, "demo_mode": False},
    ]


def test_exit_code_is_zero_only_when_every_step_passes() -> None:
    good, bad = selftest.Step("a"), selftest.Step("b")
    bad.fail("broken")

    assert selftest.finish([good], None, io.StringIO()) == 0
    assert selftest.finish([good, bad], None, io.StringIO()) == 1
    assert selftest.finish([], None, io.StringIO()) == 1


def test_table_shows_result_engine_first_audio_and_wrapped_text() -> None:
    step = selftest.Step("Auto-announce failure", seconds=8.6)
    step.spoken = [voice.SpeechReport("Ooh, a small hiccup. " * 6, "OpenAI", 0.61)]
    broken = selftest.Step("Undo last fix")
    broken.fail("Undo didn't say it was done")
    out = io.StringIO()

    selftest.print_table([step, broken], out)

    table = out.getvalue()
    assert "Auto-announce failure            PASS    8.6 s    OpenAI                0.6 s" in table
    assert "Undo last fix                    FAIL" in table
    assert "✗ Undo didn't say it was done" in table
    assert "1 of 2 steps passed." in table
    assert all(len(line) < 160 for line in table.splitlines())


def test_known_patch_fixes_exactly_the_demo_bug(tmp_path) -> None:
    (tmp_path / "shop.py").write_text(f"    {selftest.BUGGY_LINE}\n")
    ticks = []

    answer = selftest.known_patch(None, tmp_path, threading.Event(), on_tick=ticks.append)

    assert (tmp_path / "shop.py").read_text() == f"    {selftest.FIXED_LINE}\n"
    assert answer.fixed and answer.verified and "shop.py" in answer.summary
    assert ticks and ticks[0] < 0.1  # the first tick comes straight away, for the working cue


def test_a_prompt_is_only_finished_when_it_is_empty() -> None:
    assert selftest._ends_with_empty_prompt("(murmur) ada@mac shop % pytest\n1 passed\n(murmur) ada@mac shop % ")
    assert not selftest._ends_with_empty_prompt("(murmur) ada@mac shop % pytest")
    assert not selftest._ends_with_empty_prompt("collecting ...")


def test_demo_rehearsal_puts_the_settings_file_back_exactly(monkeypatch) -> None:
    from murmur import settings

    settings.PATH.write_text('{\n  "voice": "cedar"\n}\n')
    before = settings.PATH.read_bytes()

    def fake_step(name, action):
        raise RuntimeError("stop after the settings were changed")

    monkeypatch.setattr(selftest.demo, "load_recording", lambda: {"codex": {"summary": ""}})
    try:
        selftest.demo_steps(fake_step, selftest.Recorder())
    except RuntimeError:
        pass

    assert settings.PATH.read_bytes() == before
