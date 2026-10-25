"""Offline sound and speech, with streamed OpenAI voice when it is available."""

import math
import re
import struct
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sounddevice

from murmur import config, settings
from murmur import personality


_SAMPLE_RATE = 22050
_STOP_EVENT = threading.Event()
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

SOFT_SOUND_CAPTION = "♪ success sound"
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
    """The voice picked in the menu, or Marin if the saved one is unknown."""
    chosen = settings.get("voice")
    return chosen if chosen in config.VOICES else config.VOICE


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
        should_stop = stream is not None and _STOP_EVENT.is_set()
    if should_stop:
        try:
            stream.abort()
        except Exception:
            pass


def _set_active_process(process: subprocess.Popen[bytes] | None) -> None:
    global _ACTIVE_PROCESS
    with _STATE_LOCK:
        _ACTIVE_PROCESS = process
        should_stop = process is not None and _STOP_EVENT.is_set()
    if should_stop:
        try:
            process.terminate()
        except Exception:
            pass


def stop_speech() -> None:
    """Stop the current system voice or audio stream, if there is one."""
    _STOP_EVENT.set()
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


def _tone_bytes() -> bytes:
    """Make a quiet short tone without an audio file or another package."""
    frame_count = int(_SAMPLE_RATE * 0.12)
    fade_frames = int(_SAMPLE_RATE * 0.01)
    samples = bytearray()
    for frame in range(frame_count):
        fade = min(1.0, frame / fade_frames, (frame_count - frame - 1) / fade_frames)
        value = int(math.sin(2 * math.pi * 660 * frame / _SAMPLE_RATE) * 2600 * fade)
        samples.extend(struct.pack("<h", value))
    return bytes(samples)


def play_soft_sound(caption: str = SOFT_SOUND_CAPTION) -> str:
    """Play a quiet confirmation tone, using speech if audio playback fails.

    caption is what the caption window shows, since a sound has no words.
    """
    if _STOP_EVENT.is_set():
        return "none (stopped)"
    _tell_listeners(caption)
    engine = "none (stopped)"
    try:
        engine = "sounddevice (muted)" if _MUTED.is_set() else _play_soft_sound()
        return engine
    finally:
        _tell_listeners(None)
        _report(caption, engine, None)


def _play_soft_sound() -> str:
    stream = None
    try:
        stream = sounddevice.RawOutputStream(
            samplerate=_SAMPLE_RATE,
            channels=1,
            dtype="int16",
        )
        _set_active_stream(stream)
        stream.start()
        stream.write(_tone_bytes())
        stream.stop()
        return "sounddevice"
    except Exception as error:
        if not _STOP_EVENT.is_set():
            return _say_instead(
                "Command succeeded.", f"the success sound could not play ({_explain(error)})."
            )
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
    if not text or _STOP_EVENT.is_set():
        return False
    if _MUTED.is_set():
        return True  # pretend it was said
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            ["say", "-r", str(config.SYSTEM_VOICE_RATE), text],
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
        if _STOP_EVENT.is_set():
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
        speed=personality.speech_speed(mode),
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
            if _STOP_EVENT.is_set():
                return False
            if not chunk:
                continue
            if not got_audio:
                if not _MUTED.is_set():
                    output = sounddevice.RawOutputStream(
                        samplerate=24000,
                        channels=1,
                        dtype="int16",
                    )
                    _set_active_stream(output)
                    output.start()
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
        return not _STOP_EVENT.is_set()
    finally:
        winner.close()
        _set_active_stream(None)
        if output is not None:
            try:
                output.stop()
            finally:
                output.close()
    return not _STOP_EVENT.is_set()


def speak(
    text: str,
    client: Any | None = None,
    *,
    mode: personality.Mode | str | None = None,
) -> str:
    """Speak text with OpenAI streaming, falling back to the built-in Mac voice."""
    if not text or _STOP_EVENT.is_set():
        return "none (stopped)"
    global _LAST_FIRST_AUDIO, _LAST_ATTEMPT
    _LAST_FIRST_AUDIO, _LAST_ATTEMPT = None, 1
    _tell_listeners(text)
    engine = "none (stopped)"
    try:
        engine = _speak(text, client, mode)
        return engine
    finally:
        _tell_listeners(None)
        shown = f"{engine} (2nd req)" if engine == "OpenAI" and _LAST_ATTEMPT == 2 else engine
        _report(text, shown, _LAST_FIRST_AUDIO)


def _speak(text: str, client: Any | None, mode: personality.Mode | str | None) -> str:
    if not config.OPENAI_API_KEY:
        return _say_instead(text, "OPENAI_API_KEY is missing.")

    try:
        if client is None:
            client = _openai_client()
        if _stream_openai_speech(text, client, mode):
            return "OpenAI"
        if _STOP_EVENT.is_set():
            return "none (stopped)"
        return _say_instead(text, "OpenAI speech stopped early.")
    except Exception as error:
        if _STOP_EVENT.is_set():
            return "none (stopped)"
        return _say_instead(text, f"OpenAI speech failed ({_explain(error)}).")
