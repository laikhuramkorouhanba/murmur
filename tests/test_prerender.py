"""Pre-rendered speech: the right file for the right line, played before going live."""

import wave

import pytest

from murmur import config, personality, prerender, voice

from test_voice import FakeClient, FakeOutputStream, FakeSpeechCreate, FakeStreamingResponse


def write_wav(path, seconds: float = 0.2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(prerender.SAMPLE_RATE)
        recording.writeframes(b"\x01\x00" * int(prerender.SAMPLE_RATE * seconds))


def test_the_name_changes_with_the_words_and_the_style(monkeypatch) -> None:
    first = prerender.key("Leave it with me.")

    assert prerender.key("Leave it with me!") != first
    monkeypatch.setattr(config, "SMOOTH_SPEECH_SPEED", 1.2)
    assert prerender.key("Leave it with me.") != first  # a new speed means a new recording


def test_only_marin_in_smooth_is_pre_rendered() -> None:
    line = "Leave it with me."
    write_wav(prerender.path_for(line))

    assert prerender.find(line, "marin", "Smooth") is not None
    assert prerender.find(line, "cedar", "Smooth") is None
    assert prerender.find(line, "marin", "Focused") is None
    assert prerender.find("Something never rendered.", "marin", "Smooth") is None


def test_a_recording_is_played_instead_of_asking_openai(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    personality.set_mode("Smooth")
    line = "Leave it with me."
    write_wav(prerender.path_for(line))
    stream = FakeOutputStream(samplerate=24000, channels=1, dtype="int16")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **kwargs: stream)
    speech = FakeSpeechCreate(error=AssertionError("must not ask OpenAI"))
    reports = []
    monkeypatch.setattr(voice, "_REPORTERS", [reports.append])

    assert voice.speak(line, client=FakeClient(speech), mode="Smooth") == "pre-rendered"

    assert speech.arguments is None  # no request was made
    assert b"".join(stream.chunks) == b"\x01\x00" * int(prerender.SAMPLE_RATE * 0.2)
    assert reports[0].engine == "pre-rendered" and reports[0].first_audio_seconds < 0.5


def test_a_broken_recording_falls_back_to_live_speech(monkeypatch, capsys) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    line = "Leave it with me."
    path = prerender.path_for(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a wav file")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **kwargs: FakeOutputStream(**kwargs))
    speech = FakeSpeechCreate(FakeStreamingResponse([b"\x00\x00"]))

    assert voice.speak(line, client=FakeClient(speech), mode="Smooth") == "OpenAI"

    assert speech.arguments["input"] == line
    assert "couldn't be read" in capsys.readouterr().out


def test_scripted_lines_cover_the_demo_and_the_fix_cues() -> None:
    lines = prerender.scripted_lines()

    assert "Demo mode. This is a replay of a recorded run." in lines
    assert "Demo project reset. The bug is back." in lines
    assert all(cue in lines for cue in personality._FIX_LINES["working"][0])
    assert any("it expected 21.60 but got 18.40" in line for line in lines)
    assert any(line.endswith("Undo last fix is in the Murmur menu.") for line in lines)
    assert len(lines) == len(set(lines))
