import inspect

import sounddevice

from murmur import config, voice
from murmur import personality


class FakeOutputStream:
    def __init__(self, **kwargs):
        # Fail like the real thing if we pass an option sounddevice does not have.
        inspect.signature(sounddevice.RawOutputStream).bind(**kwargs)
        self.arguments = kwargs
        self.chunks: list[bytes] = []
        self.started = False
        self.stopped = False
        self.aborted = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def write(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def stop(self) -> None:
        self.stopped = True

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed = True


class FakeStreamingResponse:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def iter_bytes(self, chunk_size: int):
        assert chunk_size == 4096
        yield from self.chunks


class FakeSpeechCreate:
    def __init__(self, response: FakeStreamingResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.arguments = None

    def create(self, **arguments):
        self.arguments = arguments
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, speech_create: FakeSpeechCreate):
        self.audio = type(
            "Audio",
            (),
            {
                "speech": type(
                    "Speech",
                    (),
                    {"with_streaming_response": type("Streaming", (), {"create": speech_create.create})()},
                )()
            },
        )()


def test_soft_sound_writes_a_quiet_offline_tone(monkeypatch) -> None:
    voice.reset_stop()
    streams: list[FakeOutputStream] = []

    def make_stream(**kwargs):
        stream = FakeOutputStream(**kwargs)
        streams.append(stream)
        return stream

    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", make_stream)

    assert voice.play_soft_sound() == "sounddevice"

    assert len(streams) == 1
    assert streams[0].arguments == {
        "samplerate": 22050,
        "channels": 1,
        "dtype": "int16",
    }
    assert streams[0].chunks and len(streams[0].chunks[0]) > 0
    assert streams[0].stopped and streams[0].closed


def test_missing_key_uses_the_macos_say_command(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    commands: list[list[str]] = []

    class FakeProcess:
        def wait(self):
            return 0

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr(voice.subprocess, "Popen", fake_popen)

    assert voice.speak("The tests passed.") == "macOS say"

    assert commands == [["say", "-r", "180", "The tests passed."]]


def test_streams_openai_audio_using_marin_and_configured_speed(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    output_streams: list[FakeOutputStream] = []

    def make_stream(**kwargs):
        stream = FakeOutputStream(**kwargs)
        output_streams.append(stream)
        return stream

    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", make_stream)
    fake_response = FakeStreamingResponse([b"\x00\x00\x01\x00"])
    speech_create = FakeSpeechCreate(fake_response)
    client = FakeClient(speech_create)

    assert voice.speak("A short result.", client=client) == "OpenAI"

    assert speech_create.arguments["model"] == "gpt-4o-mini-tts"
    assert speech_create.arguments["voice"] == "marin"
    assert speech_create.arguments["speed"] == 1.0
    assert "late-night radio host" in speech_create.arguments["instructions"]
    assert speech_create.arguments["response_format"] == "pcm"
    assert speech_create.arguments["timeout"] == 5
    assert output_streams[0].chunks == [b"\x00\x00\x01\x00"]
    assert output_streams[0].closed
    assert fake_response.closed


def test_network_error_falls_back_to_macos_say(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    commands: list[list[str]] = []

    class FakeProcess:
        def wait(self):
            return 0

    monkeypatch.setattr(
        voice.subprocess,
        "Popen",
        lambda command, **_kwargs: (commands.append(command) or FakeProcess()),
    )
    client = FakeClient(FakeSpeechCreate(error=OSError("offline")))

    assert voice.speak("Exact fallback text.", client=client) == "macOS say"

    assert commands == [["say", "-r", "180", "Exact fallback text."]]


def test_no_audio_falls_back_to_macos_say(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(voice.time, "monotonic", lambda: 0)
    commands: list[list[str]] = []

    class FakeProcess:
        def wait(self):
            return 0

    monkeypatch.setattr(
        voice.subprocess,
        "Popen",
        lambda command, **_kwargs: (commands.append(command) or FakeProcess()),
    )
    client = FakeClient(FakeSpeechCreate(FakeStreamingResponse([])))

    assert voice.speak("No audio arrived.", client=client) == "macOS say"

    assert commands == [["say", "-r", "180", "No audio arrived."]]


def test_audio_late_past_five_seconds_falls_back_to_macos_say(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    times = iter((0.0, 5.1))
    monkeypatch.setattr(voice.time, "monotonic", lambda: next(times))
    commands: list[list[str]] = []

    class FakeProcess:
        def wait(self):
            return 0

    monkeypatch.setattr(
        voice.subprocess,
        "Popen",
        lambda command, **_kwargs: (commands.append(command) or FakeProcess()),
    )
    client = FakeClient(FakeSpeechCreate(FakeStreamingResponse([b"\x00\x00"])))

    assert voice.speak("Late audio.", client=client) == "macOS say"

    assert commands == [["say", "-r", "180", "Late audio."]]


def test_audio_device_failure_falls_back_to_macos_say(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    commands: list[list[str]] = []

    class FakeProcess:
        def wait(self):
            return 0

    def fail_to_open_audio(**_kwargs):
        raise OSError("no output device")

    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", fail_to_open_audio)
    monkeypatch.setattr(
        voice.subprocess,
        "Popen",
        lambda command, **_kwargs: (commands.append(command) or FakeProcess()),
    )
    client = FakeClient(FakeSpeechCreate(FakeStreamingResponse([b"\x00\x00"])))

    assert voice.speak("Use the Mac voice.", client=client) == "macOS say"

    assert commands == [["say", "-r", "180", "Use the Mac voice."]]


def test_focused_mode_uses_neutral_tts_instructions(monkeypatch) -> None:
    voice.reset_stop()
    personality.set_mode(personality.Mode.FOCUSED)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **kwargs: FakeOutputStream(**kwargs))
    speech_create = FakeSpeechCreate(FakeStreamingResponse([b"\x00\x00"]))
    client = FakeClient(speech_create)

    voice.speak("A short result.", client=client)

    assert "Clear, calm, neutral" in speech_create.arguments["instructions"]
    assert "late-night radio host" not in speech_create.arguments["instructions"]
    personality.set_mode(personality.Mode.SMOOTH)


def test_stop_speech_terminates_current_playback(monkeypatch) -> None:
    voice.reset_stop()

    class FakeProcess:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    process = FakeProcess()
    stream = FakeOutputStream()
    stopped_audio = []
    voice._ACTIVE_PROCESS = process
    voice._ACTIVE_STREAM = stream
    monkeypatch.setattr(voice.sounddevice, "stop", lambda: stopped_audio.append(True))

    voice.stop_speech()

    assert voice._STOP_EVENT.is_set()
    assert process.terminated
    assert stream.aborted
    assert stopped_audio == [True]
    voice._ACTIVE_PROCESS = None
    voice._ACTIVE_STREAM = None
    voice.reset_stop()


def test_fake_stream_rejects_options_real_sounddevice_does_not_have() -> None:
    try:
        FakeOutputStream(samplerate=24000, channels=1, dtype="int16", start=False)
    except TypeError:
        return
    raise AssertionError("start=False should be rejected, like the real RawOutputStream does")


def test_fallback_prints_the_real_reason_and_hides_the_key(monkeypatch, capsys) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

    class FakeProcess:
        def wait(self):
            return 0

    monkeypatch.setattr(voice.subprocess, "Popen", lambda command, **_kwargs: FakeProcess())
    error = PermissionError("Incorrect API key provided: sk-proj-abc123*****xyz9")
    client = FakeClient(FakeSpeechCreate(error=error))

    assert voice.speak("Hello.", client=client) == "macOS say"

    out = capsys.readouterr().out
    assert "Murmur: using macOS say because OpenAI speech failed (PermissionError:" in out
    assert "sk-[hidden]" in out
    assert "abc123" not in out


def test_missing_key_reason_is_printed(monkeypatch, capsys) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)

    class FakeProcess:
        def wait(self):
            return 0

    monkeypatch.setattr(voice.subprocess, "Popen", lambda command, **_kwargs: FakeProcess())

    voice.speak("Hello.")

    assert "Murmur: using macOS say because OPENAI_API_KEY is missing." in capsys.readouterr().out


def test_odd_sized_audio_chunks_are_rejoined_into_whole_samples(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    streams: list[FakeOutputStream] = []

    def make_stream(**kwargs):
        stream = FakeOutputStream(**kwargs)
        streams.append(stream)
        return stream

    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", make_stream)
    client = FakeClient(FakeSpeechCreate(FakeStreamingResponse([b"\x01\x02\x03", b"\x04\x05\x06"])))

    assert voice.speak("Chunky.", client=client) == "OpenAI"

    assert streams[0].chunks == [b"\x01\x02", b"\x03\x04\x05\x06"]
