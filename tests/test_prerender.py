"""Pre-rendered speech: the right file for the right line, played before going live."""

import wave

import pytest

from murmur import config, personality, prerender, settings, voice

from test_voice import FakeClient, FakeOutputStream, FakeSpeechCreate, FakeStreamingResponse


def write_wav(path, seconds: float = 0.2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(prerender.SAMPLE_RATE)
        recording.writeframes(b"\x01\x00" * int(prerender.SAMPLE_RATE * seconds))


def test_the_name_changes_with_the_words_and_the_speed() -> None:
    first = prerender.key("Fixing.", "fast")

    assert prerender.key("Fixing!", "fast") != first
    assert prerender.key("Fixing.", "maximum") != first  # a new speed means a new recording


def test_only_marin_in_brief_is_pre_rendered() -> None:
    line = "Fixing."
    write_wav(prerender.path_for(line, "fast"))

    assert prerender.find(line, "marin", "Brief", "fast") is not None
    assert prerender.find(line, "marin", "Brief", "normal") is None  # that speed wasn't rendered here
    assert prerender.find(line, "cedar", "Brief", "fast") is None
    assert prerender.find(line, "marin", "Friendly", "fast") is None
    assert prerender.find("Something never rendered.", "marin", "Brief", "fast") is None


def test_a_recording_is_played_instead_of_asking_openai(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    settings.set("engine", "natural")
    line = "Fixing."
    write_wav(prerender.path_for(line, "fast"))
    stream = FakeOutputStream(samplerate=24000, channels=1, dtype="int16")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **kwargs: stream)
    speech = FakeSpeechCreate(error=AssertionError("must not ask OpenAI"))
    reports = []
    monkeypatch.setattr(voice, "_REPORTERS", [reports.append])

    assert voice.speak(line, client=FakeClient(speech), mode="Brief") == "pre-rendered"

    assert speech.arguments is None  # no request was made
    assert b"".join(stream.chunks) == b"\x01\x00" * int(prerender.SAMPLE_RATE * 0.2)
    assert reports[0].engine == "pre-rendered" and reports[0].first_audio_seconds < 0.5


def test_a_broken_recording_falls_back_to_live_speech(monkeypatch, capsys) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    settings.set("engine", "natural")
    line = "Fixing."
    path = prerender.path_for(line, "fast")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a wav file")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **kwargs: FakeOutputStream(**kwargs))
    speech = FakeSpeechCreate(FakeStreamingResponse([b"\x00\x00"]))

    assert voice.speak(line, client=FakeClient(speech), mode="Brief") == "OpenAI"

    assert speech.arguments["input"] == line
    assert "couldn't be read" in capsys.readouterr().out


def test_scripted_lines_cover_the_demo_and_the_fix_cues() -> None:
    lines = prerender.scripted_lines()

    assert "Demo mode. This is a replay of a recorded run." in lines
    assert "Demo project reset. The bug is back." in lines
    assert personality._FIX_LINES["working"][1] in lines  # Brief's "Fixing."
    assert "Failed. shop demo check dot py, line 9. Expected 21.60, got 18.40." in lines
    assert "Murmur" in lines  # the key layer's cue
    assert any(line.endswith("Undo last fix is in the Murmur menu.") for line in lines)
    assert len(lines) == len(set(lines))


def test_every_scripted_line_has_its_recording(monkeypatch) -> None:
    """If this fails, a line's wording changed: run `uv run python -m murmur.prerender --prune`."""
    from pathlib import Path

    import murmur

    monkeypatch.setattr(config, "AUDIO_DIR", Path(murmur.__file__).parent / "audio")

    assert prerender.missing_lines() == []
