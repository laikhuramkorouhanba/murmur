"""Offline sound and speech, with streamed OpenAI voice when it is available."""

import math
import re
import struct
import subprocess
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sounddevice

from murmur import config, settings
from murmur import personality, prerender


_SAMPLE_RATE = 22050
_STOP_EVENT = threading.Event()  # stop everything until the next job starts
_HUSH_EVENT = threading.Event()  # stop just the current line (Control during a fix)
_STATE_LOCK = threading.Lock()
_ACTIVE_PROCESS: subprocess.Popen[bytes] | None = None
_ACTIVE_STREAM: Any | None = None
_CLIENT: Any | None = None
_CLIENT_LOCK = threading.Lock()
_LISTENERS: list[Callable[[str | None], None]] = []
_REPORTERS: list[Callable[["SpeechReport"], None]] = []
_MUTED = threading.Event()  # still asks OpenAI for speech (so timings are real) but plays nothing
_LAST_FIRST_AUDIO: float | None = None
_LAST_ATTEMPT = 1  # 2 when the second, parallel request won


@dataclass(frozen=True)
class SpeechReport:
    """What one line ended up as: the words, who said them, and how fast the sound started."""

    text: str
    engine: str  # "OpenAI", "macOS say", "sounddevice", "none (stopped)", ...
    first_audio_seconds: float | None  # only for OpenAI speech


def set_muted(muted: bool) -> None:
    """Muted: OpenAI speech is still requested and timed, but nothing comes out of the speakers."""
    if muted:
        _MUTED.set()
    else:
        _MUTED.clear()


def on_spoken(reporter: Callable[["SpeechReport"], None]) -> None:
    """Call reporter(SpeechReport) after each line or sound finishes. The self-test uses this."""
    _REPORTERS.append(reporter)


def _report(text: str, engine: str, first_audio: float | None) -> None:
    for reporter in list(_REPORTERS):
        try:
            reporter(SpeechReport(text, engine, first_audio))
        except Exception as error:
            print(f"Murmur: a speech reporter failed ({_explain(error)}).")

PASS_SOUND_CAPTION = "♪ pass sound"
FAIL_SOUND_CAPTION = "♪ fail sound"
TICK_CAPTION = "♪ still working"


def on_speech(listener: Callable[[str | None], None]) -> None:
    """Call listener(text) when Murmur starts saying something, and listener(None) when it's done.

    Every way Murmur makes sound goes through here: OpenAI speech, the Mac
    voice, and the soft sound. The caption window listens to this.
    """
    _LISTENERS.append(listener)


def _tell_listeners(text: str | None) -> None:
    for listener in list(_LISTENERS):
        try:
            listener(text)
        except Exception as error:
            # A broken listener must never stop the speech itself.
            print(f"Murmur: a speech listener failed ({_explain(error)}).")
_SECRET_LIKE = re.compile(r"sk-[A-Za-z0-9_\-*.]+")


def _explain(error: BaseException) -> str:
    """Turn an error into a short reason, hiding anything that looks like an API key."""
    message = str(error).strip() or "no details"
    return _SECRET_LIKE.sub("sk-[hidden]", f"{type(error).__name__}: {message}")


def _say_instead(text: str, reason: str) -> str:
    """Tell the user why OpenAI speech was skipped, then use the Mac voice."""
    print(f"Murmur: using macOS say because {reason}")
    return "macOS say" if _speak_with_system_voice(text) else "none"


def current_voice() -> str:
    """The Natural voice picked in the menu, or Marin if the saved one is unknown."""
    chosen = settings.get("voice")
    return chosen if chosen in config.VOICES else config.VOICE


def voiceover_running() -> bool:
    """Ask macOS whether VoiceOver is on right now. Cheap, so we ask before every line."""
    try:
        import AppKit

        return bool(AppKit.NSWorkspace.sharedWorkspace().isVoiceOverEnabled())
    except Exception:
        return False


def chosen_engine() -> str:
    """What the menu says: "auto", "voiceover", "mac" or "natural"."""
    chosen = settings.get("engine")
    return chosen if chosen in config.ENGINES else "auto"


def engine() -> str:
    """Who actually speaks the next line: "voiceover", "mac" or "natural".

    VoiceOver only works while it's running. If it's off, the Mac voice takes
    over, so you never end up with silence.
    """
    chosen = chosen_engine()
    if chosen in ("auto", "voiceover"):
        return "voiceover" if voiceover_running() else "mac"
    return chosen


def speed_choice() -> str:
    chosen = settings.get("speed")
    return chosen if chosen in config.SPEEDS else settings.DEFAULTS["speed"]


def natural_speed() -> float:
    """OpenAI's speed number for the chosen speed."""
    return config.NATURAL_SPEEDS[speed_choice()]


def mac_voice_rate() -> int:
    """The Mac voice's words per minute for the chosen speed."""
    return config.MAC_VOICE_RATES[speed_choice()]


def _tts_timeout() -> Any:
    from openai import Timeout

    # connect: reaching OpenAI at all. The rest: waiting for each piece of audio.
    return Timeout(config.FIRST_AUDIO_TIMEOUT_SECONDS, connect=config.TTS_CONNECT_TIMEOUT_SECONDS)


def _openai_client() -> Any:
    """One shared OpenAI client, so every line reuses the open connection.

    Making a new client for each line meant a fresh secure handshake every time,
    which is slow. We also turn off automatic retries: a retry after a timeout
    only makes the silence longer, and `say` is ready to take over straight away.
    """
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            from openai import DefaultHttpxClient, OpenAI

            try:
                # openai's own HTTP library (already installed with it). Lets an idle
                # connection stay open for a couple of minutes instead of 5 seconds.
                from httpx2 import Limits

                http_client = DefaultHttpxClient(limits=Limits(keepalive_expiry=config.TTS_KEEPALIVE_SECONDS))
            except ImportError:
                http_client = None
            _CLIENT = OpenAI(
                api_key=config.OPENAI_API_KEY,
                timeout=_tts_timeout(),
                max_retries=0,
                http_client=http_client,
            )
        return _CLIENT


def forget_client() -> None:
    """Drop the shared client, so the next line uses a newly saved API key."""
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = None


def warm_up() -> None:
    """Open the connection to OpenAI in the background at startup, so the first line starts sooner."""
    if not config.OPENAI_API_KEY:
        return

    def connect() -> None:
        try:
            _openai_client().models.retrieve(config.TTS_MODEL)
        except Exception as error:
            print(
                f"Murmur startup note: couldn't reach OpenAI ({_explain(error)}). "
                "Speech will use the Mac voice until it can."
            )

    threading.Thread(target=connect, name="murmur-voice-warm-up", daemon=True).start()


def reset_stop() -> None:
    """Clear a previous stop request before starting a new announcement."""
    _STOP_EVENT.clear()


def _set_active_stream(stream: Any | None) -> None:
    global _ACTIVE_STREAM
    with _STATE_LOCK:
        _ACTIVE_STREAM = stream
        should_stop = stream is not None and _stopped()
    if should_stop:
        try:
            stream.abort()
        except Exception:
            pass


def _set_active_process(process: subprocess.Popen[bytes] | None) -> None:
    global _ACTIVE_PROCESS
    with _STATE_LOCK:
        _ACTIVE_PROCESS = process
        should_stop = process is not None and _stopped()
    if should_stop:
        try:
            process.terminate()
        except Exception:
            pass


def _stopped() -> bool:
    return _STOP_EVENT.is_set() or _HUSH_EVENT.is_set()


def hush() -> None:
    """Silence the line playing right now, but let the next one speak.

    The Control key uses this while a fix runs: you can quiet "Fixing." without
    cancelling Codex, and you'll still hear the result.
    """
    _HUSH_EVENT.set()
    _silence_now()


def stop_speech() -> None:
    """Stop the current system voice or audio stream, if there is one."""
    _STOP_EVENT.set()
    _silence_now()


def _silence_now() -> None:
    with _STATE_LOCK:
        process = _ACTIVE_PROCESS
        stream = _ACTIVE_STREAM

    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except Exception:
            pass
    try:
        sounddevice.stop()
    except Exception:
        pass
    if stream is not None:
        try:
            stream.abort()
        except Exception:
            pass


class AudioDeviceProblem(Exception):
    """The speakers wouldn't open, even after restarting the audio system."""


def _restart_audio() -> None:
    # PortAudio (the sound library under sounddevice) remembers the audio devices it
    # saw at startup. Restarting it makes it look again, for example after headphones
    # were plugged in or Bluetooth reconnected.
    try:
        sounddevice._terminate()
        sounddevice._initialize()
    except Exception as error:
        print(f"Murmur: couldn't restart audio ({_explain(error)}).")


def _open_output(rate: int) -> Any:
    """Open the speakers for 16-bit mono sound, trying once more if the audio device has a hiccup.

    PortAudio error -9986 ("internal error") turned up in a real log. It usually
    means the output device changed. We restart the audio system and try again
    once; if that fails too, the caller uses the Mac voice, which has its own audio path.
    """
    for attempt in (1, 2):
        stream = None
        try:
            stream = sounddevice.RawOutputStream(samplerate=rate, channels=1, dtype="int16")
            _set_active_stream(stream)
            stream.start()
            return stream
        except sounddevice.PortAudioError as error:
            _set_active_stream(None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            if attempt == 2:
                raise AudioDeviceProblem(f"the audio device failed twice ({_explain(error)}).") from error
            print(f"Murmur: the audio device had a problem ({_explain(error)}); restarting audio and trying once more.")
            _restart_audio()
    raise AssertionError("unreachable")


# Two notes going up for a pass, two lower notes going down for a fail: easy to tell apart.
_PASS_NOTES = ((660.0, 0.07), (990.0, 0.09))
_FAIL_NOTES = ((330.0, 0.09), (220.0, 0.13))


def _tone_bytes(notes: tuple[tuple[float, float], ...] = _PASS_NOTES, *, buzz: bool = False) -> bytes:
    """Make a short, quiet set of notes without an audio file or another package.

    buzz adds a little roughness (a second harmonic), so the fail sound feels different, not just lower.
    """
    samples = bytearray()
    fade_frames = int(_SAMPLE_RATE * 0.008)
    for pitch, seconds in notes:
        frame_count = int(_SAMPLE_RATE * seconds)
        for frame in range(frame_count):
            fade = min(1.0, frame / fade_frames, (frame_count - frame - 1) / fade_frames)
            angle = 2 * math.pi * pitch * frame / _SAMPLE_RATE
            wave_value = math.sin(angle) + (0.35 * math.sin(2 * angle) if buzz else 0.0)
            samples.extend(struct.pack("<h", int(wave_value * 2600 * fade)))
    return bytes(samples)


_SOFT_NOTES = ((660.0, 0.12),)  # one quiet note: Fix it's "still working" tick


def play_soft_sound(caption: str = TICK_CAPTION) -> str:
    """One quiet note, used as Fix it's tick while Codex works.

    caption is what the caption window shows, since a sound has no words.
    """
    return _play_sound(_tone_bytes(_SOFT_NOTES), caption, "Still working.")


def play_pass_sound() -> str:
    """The pass sound: two quick notes going up. Played before speech about a success."""
    return _play_sound(_tone_bytes(_PASS_NOTES), PASS_SOUND_CAPTION, "Passed.")


def play_fail_sound() -> str:
    """The fail sound: two lower notes going down. Played before speech about an error."""
    return _play_sound(_tone_bytes(_FAIL_NOTES, buzz=True), FAIL_SOUND_CAPTION, "Failed.")


def _play_sound(sound: bytes, caption: str, words_instead: str) -> str:
    if _STOP_EVENT.is_set():
        return "none (stopped)"
    _HUSH_EVENT.clear()
    _tell_listeners(caption)
    used = "none (stopped)"
    try:
        used = "sounddevice (muted)" if _MUTED.is_set() else _play_tone(sound, words_instead)
        return used
    finally:
        _tell_listeners(None)
        _report(caption, used, None)


def _play_tone(sound: bytes, words_instead: str) -> str:
    stream = None
    try:
        stream = _open_output(_SAMPLE_RATE)
        stream.write(sound)
        stream.stop()
        return "sounddevice"
    except Exception as error:
        if not _stopped():
            return _say_instead(words_instead, f"the sound could not play ({_explain(error)}).")
        return "none (stopped)"
    finally:
        _set_active_stream(None)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


def _speak_with_system_voice(text: str) -> bool:
    """Use the built-in Mac voice so speech still works without the network."""
    if not text or _stopped():
        return False
    if _MUTED.is_set():
        return True  # pretend it was said
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            ["say", "-r", str(mac_voice_rate()), text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _set_active_process(process)
        return process.wait() == 0
    except (FileNotFoundError, OSError):
        return False
    finally:
        if process is not None:
            _set_active_process(None)


def _say(text: str) -> str:
    """The Mac voice on purpose (not as a fallback)."""
    if _speak_with_system_voice(text):
        return "macOS say"
    return "none (stopped)" if _stopped() else "none"


def _post_voiceover_announcement(text: str) -> None:
    """Ask VoiceOver to say text, in the user's own VoiceOver voice, rate and braille display."""
    import AppKit

    AppKit.NSAccessibilityPostNotificationWithUserInfo(
        AppKit.NSApp(),
        AppKit.NSAccessibilityAnnouncementRequestedNotification,
        {
            AppKit.NSAccessibilityAnnouncementKey: text,
            # High: VoiceOver says it even if it's in the middle of reading something else.
            AppKit.NSAccessibilityPriorityKey: AppKit.NSAccessibilityPriorityHigh,
        },
    )


def _on_main_thread(work: Callable[[], None]) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callAfter(work)  # macOS wants accessibility messages sent from the main thread


def voiceover_seconds(text: str) -> float:
    """Roughly how long VoiceOver takes to say text. It never tells us, so we guess."""
    return 0.3 + len(text) * config.VOICEOVER_SECONDS_PER_CHAR


def _announce_with_voiceover(text: str) -> str:
    if _MUTED.is_set():
        return "VoiceOver (muted)"
    try:
        _on_main_thread(lambda: _post_voiceover_announcement(text))
    except Exception as error:
        return _say_instead(text, f"VoiceOver couldn't be reached ({_explain(error)}).")
    # Wait about as long as VoiceOver takes, so the next line doesn't cut this one off.
    # Pressing Control stops VoiceOver itself; a Murmur stop ends this wait too.
    deadline = time.monotonic() + voiceover_seconds(text)
    while not _stopped() and time.monotonic() < deadline:
        time.sleep(0.05)
    return "VoiceOver"


class _Attempt:
    """One speech request, opened on its own thread, waiting for its first piece of audio."""

    def __init__(self, number: int, client: Any, request: dict[str, Any], race: "_Race") -> None:
        self.number = number
        self.manager: Any | None = None
        self.chunks: Any | None = None
        self.first_chunk = b""
        threading.Thread(
            target=self._run, args=(client, request, race), name=f"murmur-speech-{number}", daemon=True
        ).start()

    def _run(self, client: Any, request: dict[str, Any], race: "_Race") -> None:
        try:
            self.manager = client.audio.speech.with_streaming_response.create(**request)
            response = self.manager.__enter__()
            self.chunks = response.iter_bytes(chunk_size=4096)
            for chunk in self.chunks:
                if chunk:
                    self.first_chunk = chunk
                    break
            else:
                raise TimeoutError("OpenAI returned no speech audio.")
        except Exception as error:
            self.close()
            race.lost(self, error)
            return
        if not race.won(self):
            self.close()  # the other request got there first

    def close(self) -> None:
        if self.manager is not None:
            try:
                self.manager.__exit__(None, None, None)
            except Exception:
                pass
            self.manager = None


class _Race:
    """Up to two identical requests; whichever sends audio first gets played."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._done = threading.Event()
        self.winner: _Attempt | None = None
        self.errors: list[BaseException] = []
        self.started = 0
        self._closed = False

    def won(self, attempt: _Attempt) -> bool:
        with self._lock:
            if self._closed or self.winner is not None:
                return False
            self.winner = attempt
        self._done.set()
        return True

    def lost(self, attempt: _Attempt, error: BaseException) -> None:
        with self._lock:
            self.errors.append(error)
        self._done.set()  # wake the waiter, so it can start the other request straight away

    def wait(self, seconds: float) -> None:
        self._done.wait(max(0.0, seconds))
        self._done.clear()

    def give_up(self) -> None:
        """Nobody won in time: any request that finishes later just closes itself."""
        with self._lock:
            self._closed = True


def _first_audio(client: Any, request: dict[str, Any], requested_at: float) -> _Attempt | None:
    """Ask for speech; if nothing arrives within TTS_HEDGE_SECONDS, ask again in parallel.

    OpenAI usually answers in about half a second, but now and then one request
    takes many seconds. A second identical request almost always beats it. We
    play whichever starts first and close the other, so only one is ever heard.
    Returns None if speech was stopped. Raises if neither started in time.
    """
    race = _Race()
    deadline = requested_at + config.FIRST_AUDIO_TIMEOUT_SECONDS
    hedge_at = requested_at + config.TTS_HEDGE_SECONDS
    attempts = [_Attempt(1, client, request, race)]
    while True:
        if _stopped():
            race.give_up()
            return None
        if race.winner is not None:
            return race.winner
        now = time.monotonic()
        everyone_failed = len(race.errors) >= len(attempts)
        if len(attempts) == 1 and (now >= hedge_at or everyone_failed) and now < deadline:
            print(f"Murmur voice: no audio after {now - requested_at:.1f} s, asking OpenAI again in parallel")
            attempts.append(_Attempt(2, client, request, race))
            continue
        if now >= deadline or (everyone_failed and len(attempts) == 2):
            race.give_up()
            if race.winner is not None:  # it just made it
                return race.winner
            if race.errors and len(race.errors) >= len(attempts):
                raise race.errors[-1]
            raise TimeoutError(
                f"No speech audio arrived within {config.FIRST_AUDIO_TIMEOUT_SECONDS:g} seconds (asked twice)."
            )
        next_check = hedge_at if len(attempts) == 1 else deadline
        race.wait(min(next_check - now, 0.05))  # short naps, so a stop request is noticed quickly


def _stream_openai_speech(
    text: str,
    client: Any,
    mode: personality.Mode | str | None = None,
) -> bool:
    """Play raw PCM chunks as soon as the OpenAI response sends them."""
    global _LAST_FIRST_AUDIO, _LAST_ATTEMPT
    requested_at = time.monotonic()
    request = dict(
        model=config.TTS_MODEL,
        voice=current_voice(),
        input=text,
        instructions=personality.tts_instructions(mode),
        speed=natural_speed(),
        response_format="pcm",
        timeout=_tts_timeout(),
    )
    winner = _first_audio(client, request, requested_at)
    if winner is None:
        return False  # stopped while waiting

    output = None
    got_audio = False
    leftover = b""
    _LAST_FIRST_AUDIO = time.monotonic() - requested_at
    _LAST_ATTEMPT = winner.number
    which = " (second request)" if winner.number == 2 else ""
    print(f"Murmur voice: first audio after {_LAST_FIRST_AUDIO:.1f} s{which}")

    def pieces():
        yield winner.first_chunk
        yield from winner.chunks

    try:
        for chunk in pieces():
            if _stopped():
                return False
            if not chunk:
                continue
            if not got_audio:
                if not _MUTED.is_set():
                    output = _open_output(24000)  # if the speakers won't open, `say` reads the line
                got_audio = True  # only once the speaker really opened
            if output is None:
                continue  # muted: keep reading so the request finishes normally
            # Each sample is 2 bytes, so hold back a lonely last byte for the next chunk.
            chunk = leftover + chunk
            cut = len(chunk) - len(chunk) % 2
            leftover = chunk[cut:]
            if cut:
                output.write(chunk[:cut])
    except Exception as error:
        if not got_audio:
            raise  # nothing played yet, so `say` can read the whole line instead
        # Part of the line already played. Reading it all again with `say` would be
        # more confusing than a short cut, so we stop here and say why in the log.
        print(f"Murmur: OpenAI speech stopped partway ({_explain(error)}); not repeating the line.")
        return not _stopped()
    finally:
        winner.close()
        _set_active_stream(None)
        if output is not None:
            try:
                output.stop()
            finally:
                output.close()
    return not _stopped()


def _play_prerendered(path: Any, text: str) -> str | None:
    """Play a pre-rendered line. Returns None if the file can't be used, so we go live instead.

    If the speakers themselves won't work, going live wouldn't help, so the Mac voice says it.
    """
    global _LAST_FIRST_AUDIO
    started = time.monotonic()
    try:
        with wave.open(str(path), "rb") as recording:
            rate = recording.getframerate()
            frames = recording.readframes(recording.getnframes())
    except (OSError, EOFError, wave.Error) as error:
        print(f"Murmur: a pre-rendered line couldn't be read ({_explain(error)}); speaking it live instead.")
        return None
    _LAST_FIRST_AUDIO = time.monotonic() - started
    if _MUTED.is_set():
        return "pre-rendered"
    stream = None
    try:
        stream = _open_output(rate)
        step = rate // 10 * 2  # a tenth of a second at a time, so a stop is heard quickly
        for start in range(0, len(frames), step):
            if _stopped():
                return "none (stopped)"
            stream.write(frames[start : start + step])
        stream.stop()
        return "pre-rendered"
    except AudioDeviceProblem as error:
        return _say_instead(text, f"{error}")
    except Exception as error:
        if _stopped():
            return "none (stopped)"
        print(f"Murmur: a pre-rendered line couldn't play ({_explain(error)}); speaking it live instead.")
        return None
    finally:
        _set_active_stream(None)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


def speak(
    text: str,
    client: Any | None = None,
    *,
    mode: personality.Mode | str | None = None,
) -> str:
    """Speak text with the chosen engine: VoiceOver, the Mac voice, or the Natural (OpenAI) voice.

    The Natural voice falls back to the Mac voice whenever OpenAI can't help.
    """
    if not text or _STOP_EVENT.is_set():
        return "none (stopped)"
    _HUSH_EVENT.clear()  # a Control press only silences the line that was playing then
    global _LAST_FIRST_AUDIO, _LAST_ATTEMPT
    _LAST_FIRST_AUDIO, _LAST_ATTEMPT = None, 1
    _tell_listeners(text)
    used = "none (stopped)"
    try:
        which = engine()
        if which == "voiceover":
            used = _announce_with_voiceover(text)
        elif which == "mac":
            used = _say(text)
        else:
            # 1. a pre-rendered file, 2. live OpenAI speech, 3. the Mac voice (inside _speak).
            recording = prerender.find(text, current_voice(), mode, speed_choice())
            used = (_play_prerendered(recording, text) if recording else None) or _speak(text, client, mode)
        return used
    finally:
        _tell_listeners(None)
        shown = f"{used} (2nd req)" if used == "OpenAI" and _LAST_ATTEMPT == 2 else used
        _report(text, shown, _LAST_FIRST_AUDIO)


def _speak(text: str, client: Any | None, mode: personality.Mode | str | None) -> str:
    if not config.OPENAI_API_KEY:
        return _say_instead(text, "OPENAI_API_KEY is missing.")

    try:
        if client is None:
            client = _openai_client()
        if _stream_openai_speech(text, client, mode):
            return "OpenAI"
        if _stopped():
            return "none (stopped)"
        return _say_instead(text, "OpenAI speech stopped early.")
    except Exception as error:
        if _stopped():
            return "none (stopped)"
        return _say_instead(text, f"OpenAI speech failed ({_explain(error)}).")
