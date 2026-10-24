"""Offline sound and speech, with streamed OpenAI voice when it is available."""

import math
import struct
import subprocess
import threading
import time
from typing import Any

import sounddevice

from murmur import config
from murmur import personality


_SAMPLE_RATE = 22050
_STOP_EVENT = threading.Event()
_STATE_LOCK = threading.Lock()
_ACTIVE_PROCESS: subprocess.Popen[bytes] | None = None
_ACTIVE_STREAM: Any | None = None


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


def play_soft_sound() -> str:
    """Play a quiet confirmation tone, using speech if audio playback fails."""
    if _STOP_EVENT.is_set():
        return "none (stopped)"
    stream = None
    try:
        stream = sounddevice.RawOutputStream(
            samplerate=_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            start=False,
        )
        _set_active_stream(stream)
        stream.start()
        stream.write(_tone_bytes())
        stream.stop()
        return "sounddevice"
    except Exception:
        if not _STOP_EVENT.is_set():
            return "macOS say" if _speak_with_system_voice("Command succeeded.") else "none"
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


def _stream_openai_speech(
    text: str,
    client: Any,
    mode: personality.Mode | str | None = None,
) -> bool:
    """Play raw PCM chunks as soon as the OpenAI response sends them."""
    requested_at = time.monotonic()
    output = None
    got_audio = False
    request = client.audio.speech.with_streaming_response.create(
        model=config.TTS_MODEL,
        voice=config.VOICE,
        input=text,
        instructions=personality.tts_instructions(mode),
        speed=config.SPEECH_SPEED,
        response_format="pcm",
        timeout=config.FIRST_AUDIO_TIMEOUT_SECONDS,
    )

    try:
        with request as response:
            for chunk in response.iter_bytes(chunk_size=4096):
                if _STOP_EVENT.is_set():
                    return False
                if not chunk:
                    continue
                if not got_audio:
                    if time.monotonic() - requested_at > config.FIRST_AUDIO_TIMEOUT_SECONDS:
                        raise TimeoutError("No speech audio arrived within five seconds.")
                    output = sounddevice.RawOutputStream(
                        samplerate=24000,
                        channels=1,
                        dtype="int16",
                        start=False,
                    )
                    _set_active_stream(output)
                    output.start()
                    got_audio = True
                output.write(chunk)
        if not got_audio and not _STOP_EVENT.is_set():
            raise TimeoutError("OpenAI returned no speech audio.")
        return not _STOP_EVENT.is_set()
    finally:
        _set_active_stream(None)
        if output is not None:
            try:
                output.stop()
            finally:
                output.close()


def speak(
    text: str,
    client: Any | None = None,
    *,
    mode: personality.Mode | str | None = None,
) -> str:
    """Speak text with OpenAI streaming, falling back to the built-in Mac voice."""
    if not text or _STOP_EVENT.is_set():
        return "none (stopped)"
    if not config.OPENAI_API_KEY:
        return "macOS say" if _speak_with_system_voice(text) else "none"

    try:
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=config.OPENAI_API_KEY,
                timeout=config.TTS_TIMEOUT_SECONDS,
            )
        if _stream_openai_speech(text, client, mode):
            return "OpenAI"
    except Exception:
        if _STOP_EVENT.is_set():
            return "none (stopped)"
    return "macOS say" if _speak_with_system_voice(text) else "none"
