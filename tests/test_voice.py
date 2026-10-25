import inspect
import threading
import time

import pytest

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
    assert speech_create.arguments["speed"] == 1.1  # Smooth is a little brisker
    assert "friendly radio host" in speech_create.arguments["instructions"]
    assert "unhurried" not in speech_create.arguments["instructions"]
    assert speech_create.arguments["response_format"] == "pcm"
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


class SlowResponse(FakeStreamingResponse):
    """A response that waits before its first audio, like a slow OpenAI start."""

    def __init__(self, chunks, delay: float, error: Exception | None = None):
        super().__init__(chunks)
        self.delay = delay
        self.error = error

    def iter_bytes(self, chunk_size: int):
        time.sleep(self.delay)
        if self.error:
            raise self.error
        yield from self.chunks


class Requests:
    """Hands out one prepared response per speech request, and counts the requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.count = 0

    def create(self, **arguments):
        self.count += 1
        return self.responses.pop(0)


@pytest.fixture
def quick_race(monkeypatch):
    """Shrink the race: second request after 0.1 s, give up at 0.6 s."""
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "TTS_HEDGE_SECONDS", 0.1)
    monkeypatch.setattr(config, "FIRST_AUDIO_TIMEOUT_SECONDS", 0.6)
    stream = FakeOutputStream(samplerate=24000, channels=1, dtype="int16")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **_kwargs: stream)
    said = []

    class FakeProcess:
        def wait(self):
            return 0

    monkeypatch.setattr(voice.subprocess, "Popen", lambda command, **_kwargs: said.append(command[-1]) or FakeProcess())
    return stream, said


def test_a_quick_start_sends_only_one_request(quick_race) -> None:
    stream, _ = quick_race
    requests = Requests(SlowResponse([b"\x01\x00"], delay=0.0))

    assert voice.speak("Fast.", client=FakeClient(requests)) == "OpenAI"

    assert requests.count == 1
    assert stream.chunks == [b"\x01\x00"]


def test_a_slow_start_is_beaten_by_a_second_request(quick_race, capsys) -> None:
    stream, said = quick_race
    slow = SlowResponse([b"\x01\x00"], delay=0.5)
    fast = SlowResponse([b"\x02\x00"], delay=0.0)
    requests = Requests(slow, fast)
    reports = []
    voice.on_spoken(reports.append)
    try:
        assert voice.speak("Race.", client=FakeClient(requests)) == "OpenAI"
    finally:
        voice._REPORTERS.remove(reports.append)

    assert requests.count == 2
    assert stream.chunks == [b"\x02\x00"]  # only the second request was played
    assert said == []  # no macOS say
    time.sleep(0.6)
    assert slow.closed  # the loser was closed when it finally answered
    out = capsys.readouterr().out
    assert "asking OpenAI again in parallel" in out and "(second request)" in out
    assert reports[-1].engine == "OpenAI (2nd req)"


def test_if_the_first_request_fails_the_second_starts_at_once(quick_race) -> None:
    stream, said = quick_race
    requests = Requests(SlowResponse([], delay=0.0, error=OSError("connection reset")), SlowResponse([b"\x03\x00"], delay=0.0))

    started = time.monotonic()
    assert voice.speak("Retry.", client=FakeClient(requests)) == "OpenAI"

    assert time.monotonic() - started < 0.1  # didn't wait for the 0.1 s mark
    assert stream.chunks == [b"\x03\x00"] and said == []


def test_say_takes_over_only_when_neither_starts_in_time(quick_race) -> None:
    stream, said = quick_race
    requests = Requests(SlowResponse([b"\x01\x00"], delay=1.0), SlowResponse([b"\x02\x00"], delay=1.0))

    started = time.monotonic()
    assert voice.speak("Too slow.", client=FakeClient(requests)) == "macOS say"

    assert 0.55 < time.monotonic() - started < 0.9  # gave up at the 0.6 s limit
    assert requests.count == 2
    assert said == ["Too slow."] and stream.chunks == []


def test_stopping_during_the_wait_plays_nothing(quick_race) -> None:
    stream, said = quick_race
    requests = Requests(SlowResponse([b"\x01\x00"], delay=0.3), SlowResponse([b"\x02\x00"], delay=0.3))
    threading.Timer(0.05, voice.stop_speech).start()

    assert voice.speak("Stop me.", client=FakeClient(requests)) == "none (stopped)"

    assert stream.chunks == [] and said == []
    voice.reset_stop()


def test_focused_keeps_normal_speed_and_smooth_is_brisker() -> None:
    from murmur import personality

    assert personality.speech_speed("Smooth") == 1.1
    assert personality.speech_speed("Focused") == 1.0
    assert "unhurried" not in personality.tts_instructions("Smooth")
    assert "warm" in personality.tts_instructions("Smooth").lower()


class BreaksPartway(FakeStreamingResponse):
    def iter_bytes(self, chunk_size: int):
        yield b"\x01\x02"
        raise TimeoutError("the network went quiet")


def test_a_line_cut_off_partway_is_not_read_again_by_say(monkeypatch, capsys) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    stream = FakeOutputStream(samplerate=24000, channels=1, dtype="int16")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **_kwargs: stream)
    monkeypatch.setattr(voice.subprocess, "Popen", lambda *a, **k: pytest.fail("say should not repeat the line"))
    client = FakeClient(FakeSpeechCreate(BreaksPartway([])))

    assert voice.speak("Half a line.", client=client) == "OpenAI"

    assert stream.chunks == [b"\x01\x02"] and stream.closed
    assert "stopped partway (TimeoutError: the network went quiet); not repeating the line" in capsys.readouterr().out


def test_speech_request_uses_the_short_connect_and_first_audio_timeouts(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **kwargs: FakeOutputStream(**kwargs))
    speech_create = FakeSpeechCreate(FakeStreamingResponse([b"\x00\x00"]))

    voice.speak("Hello.", client=FakeClient(speech_create))

    timeout = speech_create.arguments["timeout"]
    assert timeout.connect == config.TTS_CONNECT_TIMEOUT_SECONDS == 2.0
    assert timeout.read == config.FIRST_AUDIO_TIMEOUT_SECONDS == 3.5


def test_one_openai_client_is_shared_and_never_retries(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(voice, "_CLIENT", None)

    first = voice._openai_client()

    assert voice._openai_client() is first
    assert first.max_retries == 0


def test_warm_up_failure_is_a_gentle_note(monkeypatch, capsys) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

    class Offline:
        class models:
            @staticmethod
            def retrieve(_model):
                raise OSError("no route to host")

    class RunNow:
        def __init__(self, *, target, name, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(voice, "_openai_client", lambda: Offline)
    monkeypatch.setattr(voice.threading, "Thread", RunNow)

    voice.warm_up()

    assert "couldn't reach OpenAI (OSError: no route to host)" in capsys.readouterr().out


def test_warm_up_does_nothing_without_a_key(monkeypatch) -> None:
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    monkeypatch.setattr(voice, "_openai_client", lambda: pytest.fail("should not connect"))

    voice.warm_up()


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


def test_selected_voice_is_sent_to_openai(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **kwargs: FakeOutputStream(**kwargs))
    voice.settings.set("voice", "cedar")
    speech_create = FakeSpeechCreate(FakeStreamingResponse([b"\x00\x00"]))

    voice.speak("Hello.", client=FakeClient(speech_create))

    assert speech_create.arguments["voice"] == "cedar"


def test_unknown_saved_voice_falls_back_to_marin() -> None:
    voice.settings.set("voice", "robot")

    assert voice.current_voice() == "marin"


def listen(monkeypatch) -> list:
    heard: list = []
    monkeypatch.setattr(voice, "_LISTENERS", [heard.append])
    return heard


def fake_say(monkeypatch) -> None:
    class FakeProcess:
        def wait(self):
            return 0

    monkeypatch.setattr(voice.subprocess, "Popen", lambda command, **_kwargs: FakeProcess())


def test_listeners_hear_when_speech_starts_and_ends(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    fake_say(monkeypatch)
    heard = listen(monkeypatch)

    voice.speak("Two tests failed.")

    assert heard == ["Two tests failed.", None]


def test_listeners_hear_the_soft_sound_and_ticks_as_captions(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **kwargs: FakeOutputStream(**kwargs))
    heard = listen(monkeypatch)

    voice.play_soft_sound()
    voice.play_soft_sound(voice.TICK_CAPTION)

    assert heard == ["♪ success sound", None, "♪ still working", None]


def test_nothing_is_announced_when_speech_is_stopped(monkeypatch) -> None:
    heard = listen(monkeypatch)
    voice.stop_speech()
    try:
        voice.speak("Never said.")
        voice.play_soft_sound()
    finally:
        voice.reset_stop()

    assert heard == []


def test_a_broken_listener_never_stops_the_speech(monkeypatch, capsys) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    fake_say(monkeypatch)

    def broken(_text):
        raise ValueError("oops")

    monkeypatch.setattr(voice, "_LISTENERS", [broken])

    assert voice.speak("Still spoken.") == "macOS say"
    assert "a speech listener failed (ValueError: oops)" in capsys.readouterr().out


def test_listeners_hear_the_end_even_when_speech_fails(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    heard = listen(monkeypatch)

    def no_say(*_a, **_k):
        raise RuntimeError("audio exploded")

    monkeypatch.setattr(voice, "_speak", no_say)

    with pytest.raises(RuntimeError):
        voice.speak("Boom.")
    assert heard == ["Boom.", None]  # the caption still clears


def test_muted_still_asks_openai_and_reports_timing_but_plays_nothing(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **_kwargs: pytest.fail("muted must not play"))
    reports = []
    monkeypatch.setattr(voice, "_REPORTERS", [reports.append])
    speech_create = FakeSpeechCreate(FakeStreamingResponse([b"\x00\x00", b"\x01\x00"]))
    voice.set_muted(True)
    try:
        assert voice.speak("Quiet please.", client=FakeClient(speech_create)) == "OpenAI"
        assert voice.play_soft_sound() == "sounddevice (muted)"
    finally:
        voice.set_muted(False)

    assert speech_create.arguments["input"] == "Quiet please."
    assert reports[0].text == "Quiet please." and reports[0].engine == "OpenAI"
    assert reports[0].first_audio_seconds is not None
    assert reports[1].engine == "sounddevice (muted)"


def test_muted_mac_voice_is_not_run(monkeypatch) -> None:
    voice.reset_stop()
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    monkeypatch.setattr(voice.subprocess, "Popen", lambda *a, **k: pytest.fail("muted must not run say"))
    voice.set_muted(True)
    try:
        assert voice.speak("Quiet.") == "macOS say"
    finally:
        voice.set_muted(False)
