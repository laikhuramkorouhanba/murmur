"""Pre-rendered speech: Murmur's scripted lines, recorded once with OpenAI.

On stage the venue Wi-Fi can't be trusted, so every line Murmur always says the
same way (Demo mode's whole script, the Fix it cues, the welcome)
is saved as a small WAV file in src/murmur/audio/. Speaking tries, in order:
  1. a pre-rendered file (instant, no network)
  2. live OpenAI speech
  3. the macOS `say` voice

Only the Natural voice (Marin) is pre-rendered, at the default speed: a line said
at another speed is spoken live (the Mac voice works offline anyway). Each file is named after a
hash of everything that changes how it sounds (model, voice, style, speed and the
exact words), so a changed line never plays an old recording: it just goes live.

    uv run python -m murmur.prerender           # render any missing lines (needs the API key)
    uv run python -m murmur.prerender --check   # list missing lines, exit 1 if any
    uv run python -m murmur.prerender --prune   # also delete files no line uses any more
"""

import argparse
import hashlib
import sys
import wave
from pathlib import Path

from murmur import config, personality, pronounce

VOICE = "marin"
MODE = personality.Mode.BRIEF
SAMPLE_RATE = 24000  # what OpenAI's "pcm" format sends: 24 kHz, 16-bit, mono


def key(text: str, speed: float) -> str:
    # Named after the words as spoken (after the pronunciation pass), so a new pronunciation
    # rule means a new recording, never an old one with the old sound.
    recipe = "\n".join(
        (config.TTS_MODEL, VOICE, personality.tts_instructions(MODE), f"{speed:.2f}", pronounce.spoken(text))
    )
    return hashlib.sha256(recipe.encode("utf-8")).hexdigest()[:20]


def path_for(text: str, speed: float) -> Path:
    return config.AUDIO_DIR / f"{key(text, speed)}.wav"


def find(text: str, voice_name: str, mode: personality.Mode | str | None, speed: float) -> Path | None:
    """The pre-rendered file for this exact line, or None (wrong voice, style or speed, or not rendered)."""
    selected = personality.Mode(mode) if mode is not None else personality.get_mode()
    if voice_name != VOICE or selected is not MODE:
        return None
    path = path_for(text, speed)
    return path if path.exists() else None


def scripted_lines() -> list[str]:
    """Every line Murmur speaks on its own, word for word the same each time, in Brief."""
    # Imported here so that loading this file stays light for voice.py.
    from murmur import demo, fixer, welcome
    from murmur.cleaner import clean
    from murmur.decision import brief_failure

    brief = {kind: lines[1] for kind, lines in personality._FIX_LINES.items()}
    lines = [
        brief["working"],
        brief["still"],
        "Done.",
        personality.sample_sentence_for(MODE),
        "Murmur",  # the key layer's "I'm listening" cue
        "Nothing to fix. The last command didn't fail.",
        "There's no fix to undo.",
        "There's no output to read.",
        demo.RESET_LINE,
        demo.INTRO_LINE,
    ]
    lines.append(welcome.welcome_text(MODE, app_name="Murmur", missing=[], tap="Right Command twice"))

    # Demo mode's own lines, built by the same code that will speak them.
    recording = demo.load_recording()
    lines.append(brief_failure(clean(recording["output"])))
    answer = fixer.CodexAnswer(**recording["codex"])
    lines.append(fixer.spoken_fix_summary(recording["files"], answer, opener=brief["fixed"]))
    lines.append(f"Undone. {fixer._file_list(recording['files'])} is back how it was.")
    return sorted(set(lines))


def default_speed() -> float:
    """OpenAI's speed at the default slider position: the one speed we pre-render."""
    low, high = config.NATURAL_SPEED_RANGE
    return round(low + (high - low) * config.SPEED_DEFAULT, 2)


def wanted() -> list[tuple[str, float]]:
    """Every (line, speed) pair that should have a file: each scripted line at the default speed."""
    return [(line, default_speed()) for line in scripted_lines()]


def missing_lines() -> list[tuple[str, float]]:
    return [(line, speed) for line, speed in wanted() if not path_for(line, speed).exists()]


def render(text: str, speed: float, client) -> Path:
    """Ask OpenAI for one line and save it as a WAV file."""
    audio = bytearray()
    with client.audio.speech.with_streaming_response.create(
        model=config.TTS_MODEL,
        voice=VOICE,
        input=pronounce.spoken(text),
        instructions=personality.tts_instructions(MODE),
        speed=speed,
        response_format="pcm",
        timeout=60,
    ) as response:
        for chunk in response.iter_bytes(chunk_size=65536):
            audio += chunk
    if len(audio) < SAMPLE_RATE // 2:  # under a quarter of a second of sound is not a real line
        raise RuntimeError(f"OpenAI sent almost no audio for: {text}")
    path = path_for(text, speed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(bytes(audio[: len(audio) - len(audio) % 2]))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m murmur.prerender", description="Render Murmur's scripted lines.")
    parser.add_argument("--check", action="store_true", help="only list missing lines")
    parser.add_argument("--prune", action="store_true", help="delete files that no line uses any more")
    options = parser.parse_args(argv)

    missing = missing_lines()
    total = len(wanted())
    if options.check:
        for line, speed in missing:
            print(f"missing (speed {speed}): {line}")
        print(f"{total - len(missing)} of {total} files are pre-rendered.")
        return 1 if missing else 0

    if missing:
        if not config.OPENAI_API_KEY:
            print("No OpenAI API key, so nothing can be rendered.")
            return 1
        from murmur import voice

        client = voice._openai_client()
        for number, (line, speed) in enumerate(missing, 1):
            render(line, speed, client)
            print(f"[{number}/{len(missing)}] (speed {speed}) {line}")
    if options.prune:
        names = {path_for(line, speed).name for line, speed in wanted()}
        for old in config.AUDIO_DIR.glob("*.wav"):
            if old.name not in names:
                old.unlink()
                print(f"removed unused {old.name}")
    print(f"All {total} lines (at speed {default_speed()}) are in {config.AUDIO_DIR}.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
