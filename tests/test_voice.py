import inspect
import threading
import time

import pytest

import sounddevice

from murmur import config, settings, voice
from murmur import personality


@pytest.fixture(autouse=True)
def natural_voice():
    """Most of these tests are about the Natural (OpenAI) voice, so pick it."""
    settings.set("engine", "natural")


_REAL_OUTPUT_STREAM = sounddevice.RawOutputStream  # saved before conftest swaps in a silent one


class FakeOutputStream:
    def __init__(self, **kwargs):
        # Fail like the real thing if we pass an option sounddevice does not have.
        inspect.signature(_REAL_OUTPUT_STREAM).bind(**kwargs)
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

    assert commands == [["say", "-r", "300", "The tests passed."]]


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
    assert speech_create.arguments["speed"] == 1.5  # Fast, the default speed
    assert "Clear, neutral and brisk" in speech_create.arguments["instructions"]  # Brief, the default
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

    assert commands == [["say", "-r", "300", "Exact fallback text."]]


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

    assert commands == [["say", "-r", "300", "No audio arrived."]]


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


def test_each_speed_maps_to_a_real_number_for_each_engine() -> None:
    from murmur import settings

    expected = {"normal": (1.0, 200), "fast": (1.5, 300), "very_fast": (2.0, 380), "maximum": (3.0, 450)}
    for choice, (natural, mac) in expected.items():
        settings.set("speed", choice)
        assert (voice.natural_speed(), voice.mac_voice_rate()) == (natural, mac)
    settings.set("speed", "warp")  # unknown: back to Fast
    assert voice.speed_choice() == "fast"
    assert "unhurried" not in personality.tts_instructions("Friendly")
    assert "warm" in personality.tts_instructions("Friendly").lower()


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
        raise sounddevice.PortAudioError("Error starting stream: Internal PortAudio error [PaErrorCode -9986]")

    monkeypatch.setattr(voice, "_restart_audio", lambda: None)

    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", fail_to_open_audio)
    monkeypatch.setattr(
        voice.subprocess,
        "Popen",
        lambda command, **_kwargs: (commands.append(command) or FakeProcess()),
    )
    client = FakeClient(FakeSpeechCreate(FakeStreamingResponse([b"\x00\x00"])))

    assert voice.speak("Use the Mac voice.", client=client) == "macOS say"

    assert commands == [["say", "-r", "300", "Use the Mac voice."]]


def test_brief_mode_uses_neutral_tts_instructions(monkeypatch) -> None:
    voice.reset_stop()
    personality.set_mode(personality.Mode.BRIEF)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", lambda **kwargs: FakeOutputStream(**kwargs))
    speech_create = FakeSpeechCreate(FakeStreamingResponse([b"\x00\x00"]))
    client = FakeClient(speech_create)

    voice.speak("A short result.", client=client)

    assert "Clear, neutral and brisk" in speech_create.arguments["instructions"]
    assert "late-night radio host" not in speech_create.arguments["instructions"]
    personality.set_mode(personality.Mode.FRIENDLY)


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

    voice.play_pass_sound()
    voice.play_fail_sound()
    voice.play_soft_sound(voice.TICK_CAPTION)

    assert heard == ["♪ pass sound", None, "♪ fail sound", None, "♪ still working", None]


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


def test_pass_and_fail_sounds_are_short_and_clearly_different() -> None:
    passed = voice._tone_bytes(voice._PASS_NOTES)
    failed = voice._tone_bytes(voice._FAIL_NOTES, buzz=True)

    # 16-bit samples at 22050 per second: both well under a quarter of a second.
    assert len(passed) / 2 / 22050 < 0.25 and len(failed) / 2 / 22050 < 0.25
    assert passed != failed
    assert voice._PASS_NOTES[0][0] < voice._PASS_NOTES[1][0]  # pass goes up
    assert voice._FAIL_NOTES[0][0] > voice._FAIL_NOTES[1][0]  # fail goes down


def test_the_mac_voice_engine_uses_say_at_the_chosen_rate(monkeypatch) -> None:
    from murmur import settings

    voice.reset_stop()
    settings.set("engine", "mac")
    settings.set("speed", "maximum")
    commands = []

    class FakeProcess:
        def wait(self):
            return 0

    monkeypatch.setattr(voice.subprocess, "Popen", lambda command, **_kwargs: commands.append(command) or FakeProcess())
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")

    assert voice.speak("Failed.") == "macOS say"
    assert commands == [["say", "-r", "450", "Failed."]]


def test_voiceover_is_the_default_while_it_is_running(monkeypatch) -> None:
    from murmur import settings

    settings.set("engine", "auto")
    monkeypatch.setattr(voice, "voiceover_running", lambda: True)
    assert voice.engine() == "voiceover"
    monkeypatch.setattr(voice, "voiceover_running", lambda: False)
    assert voice.engine() == "mac"  # VoiceOver off: the Mac voice, never silence

    settings.set("engine", "voiceover")
    assert voice.engine() == "mac"
    settings.set("engine", "natural")
    assert voice.engine() == "natural"


def test_voiceover_engine_posts_an_announcement_and_waits_about_as_long_as_it_talks(monkeypatch) -> None:
    from murmur import settings

    voice.reset_stop()
    settings.set("engine", "voiceover")
    monkeypatch.setattr(voice, "voiceover_running", lambda: True)
    posted = []
    monkeypatch.setattr(voice, "_on_main_thread", lambda work: work())
    monkeypatch.setattr(voice, "_post_voiceover_announcement", posted.append)
    monkeypatch.setattr(config, "VOICEOVER_SECONDS_PER_CHAR", 0.0)
    monkeypatch.setattr(voice, "voiceover_seconds", lambda text: 0.01)

    assert voice.speak("Failed. shop.py, line 9.") == "VoiceOver"
    assert posted == ["Failed. shop.py, line 9."]


def test_voiceover_posting_goes_through_the_accessibility_announcement(monkeypatch) -> None:
    import AppKit

    calls = []
    monkeypatch.setattr(AppKit, "NSAccessibilityPostNotificationWithUserInfo", lambda *args: calls.append(args))

    voice._post_voiceover_announcement("Passed.")

    element, notification, info = calls[0]
    assert notification == AppKit.NSAccessibilityAnnouncementRequestedNotification
    assert info[AppKit.NSAccessibilityAnnouncementKey] == "Passed."
    assert info[AppKit.NSAccessibilityPriorityKey] == AppKit.NSAccessibilityPriorityHigh


def test_an_audio_device_hiccup_is_retried_once_after_restarting_audio(monkeypatch, capsys) -> None:
    voice.reset_stop()
    restarts = []
    tries = []

    def flaky(**kwargs):
        tries.append(kwargs)
        if len(tries) == 1:
            raise sounddevice.PortAudioError("Internal PortAudio error [PaErrorCode -9986]")
        return FakeOutputStream(**kwargs)

    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", flaky)
    monkeypatch.setattr(voice, "_restart_audio", lambda: restarts.append(True))

    assert voice.play_pass_sound() == "sounddevice"
    assert len(tries) == 2 and restarts == [True]
    assert "trying once more" in capsys.readouterr().out


def test_a_second_audio_failure_uses_the_mac_voice_for_the_line(monkeypatch) -> None:
    voice.reset_stop()
    commands = []

    class FakeProcess:
        def wait(self):
            return 0

    def broken(**_kwargs):
        raise sounddevice.PortAudioError("Internal PortAudio error [PaErrorCode -9986]")

    monkeypatch.setattr(voice.sounddevice, "RawOutputStream", broken)
    monkeypatch.setattr(voice, "_restart_audio", lambda: None)
    monkeypatch.setattr(voice.subprocess, "Popen", lambda command, **_kwargs: commands.append(command) or FakeProcess())

    assert voice.play_fail_sound() == "macOS say"
    assert commands == [["say", "-r", "300", "Failed."]]



def test_expected_seconds_follow_the_engine_and_speed() -> None:
    from murmur import settings

    line = "x" * 120
    settings.set("engine", "mac")
    settings.set("speed", "fast")
    assert voice.expected_seconds(line) == pytest.approx(4.0)  # 300 words a minute: 30 characters a second
    settings.set("speed", "maximum")
    assert voice.expected_seconds(line) == pytest.approx(3.0)  # say tops out near 40 a second
    settings.set("engine", "natural")
    assert voice.expected_seconds(line) == pytest.approx(0.5 + 120 / 26)
