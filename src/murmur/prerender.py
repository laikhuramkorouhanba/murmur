"""Pre-rendered speech: Murmur's scripted lines, recorded once with OpenAI.

On stage the venue Wi-Fi can't be trusted, so every line Murmur always says the
same way (Demo mode's whole script, the Fix it cues, the done lines, the welcome)
is saved as a small WAV file in src/murmur/audio/. Speaking tries, in order:
  1. a pre-rendered file (instant, no network)
  2. live OpenAI speech
  3. the macOS `say` voice

Only the Marin voice in Smooth style is pre-rendered. Each file is named after a
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

from murmur import config, personality

VOICE = "marin"
MODE = personality.Mode.SMOOTH
SAMPLE_RATE = 24000  # what OpenAI's "pcm" format sends: 24 kHz, 16-bit, mono


def key(text: str) -> str:
    recipe = "\n".join(
        (config.TTS_MODEL, VOICE, personality.tts_instructions(MODE), str(personality.speech_speed(MODE)), text)
    )
    return hashlib.sha256(recipe.encode("utf-8")).hexdigest()[:20]


def path_for(text: str) -> Path:
    return config.AUDIO_DIR / f"{key(text)}.wav"


def find(text: str, voice_name: str, mode: personality.Mode | str | None) -> Path | None:
    """The pre-rendered file for this exact line, or None (wrong voice or style, or not rendered)."""
    selected = personality.Mode(mode) if mode is not None else personality.get_mode()
    if voice_name != VOICE or selected is not MODE:
        return None
    path = path_for(text)
    return path if path.exists() else None


def scripted_lines() -> list[str]:
    """Every line Murmur speaks on its own, word for word the same each time, in Smooth."""
    # Imported here so that loading this file stays light for voice.py.
    from murmur import config as cfg, demo, fixer, welcome
    from murmur.cleaner import clean
    from murmur.decision import speech_failure_summary

    lines: list[str] = []
    smooth = personality._FIX_LINES
    lines += smooth["working"][0] + smooth["still"][0]
    lines += personality._SMOOTH_DONE_LINES
    lines.append(personality.sample_sentence_for(MODE))
    lines += [
        "Nothing to fix. The last command didn't fail.",
        "There's no fix to undo.",
        "There's no output to read.",
        demo.RESET_LINE,
        demo.INTRO_LINE,
    ]
    for tap_name in cfg.DOUBLE_TAP_KEYS.values():
        lines.append(welcome.welcome_text(MODE, app_name="Murmur", missing=[], tap=f"{tap_name} twice"))

    # Demo mode's own lines, built by the same code that will speak them.
    recording = demo.load_recording()
    facts = speech_failure_summary(clean(recording["output"]), "")
    for openers, closers in (
        (personality._PLAYFUL_OPENERS, personality._PLAYFUL_CLOSERS),
        (personality._ENCOURAGING_OPENERS, personality._ENCOURAGING_CLOSERS),
    ):
        lines += [f"{opener} {facts} {closer}" for opener in openers for closer in closers]
    answer = fixer.CodexAnswer(**recording["codex"])
    lines += [fixer.spoken_fix_summary(recording["files"], answer, opener=opener) for opener in smooth["fixed"][0]]
    lines.append(f"Undone. {fixer._file_list(recording['files'])} is back how it was.")
    return sorted(set(lines))


def missing_lines() -> list[str]:
    return [line for line in scripted_lines() if not path_for(line).exists()]


def render(text: str, client) -> Path:
    """Ask OpenAI for one line and save it as a WAV file."""
    audio = bytearray()
    with client.audio.speech.with_streaming_response.create(
        model=config.TTS_MODEL,
        voice=VOICE,
        input=text,
        instructions=personality.tts_instructions(MODE),
        speed=personality.speech_speed(MODE),
        response_format="pcm",
        timeout=60,
    ) as response:
        for chunk in response.iter_bytes(chunk_size=65536):
            audio += chunk
    if len(audio) < SAMPLE_RATE:  # less than half a second of sound is not a real line
        raise RuntimeError(f"OpenAI sent almost no audio for: {text}")
    path = path_for(text)
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
    if options.check:
        for line in missing:
            print(f"missing: {line}")
        print(f"{len(scripted_lines()) - len(missing)} of {len(scripted_lines())} lines are pre-rendered.")
        return 1 if missing else 0

    if missing:
        if not config.OPENAI_API_KEY:
            print("No OpenAI API key, so nothing can be rendered.")
            return 1
        from murmur import voice

        client = voice._openai_client()
        for number, line in enumerate(missing, 1):
            render(line, client)
            print(f"[{number}/{len(missing)}] {line}")
    if options.prune:
        wanted = {path_for(line).name for line in scripted_lines()}
        for old in config.AUDIO_DIR.glob("*.wav"):
            if old.name not in wanted:
                old.unlink()
                print(f"removed unused {old.name}")
    print(f"All {len(scripted_lines())} scripted lines are pre-rendered in {config.AUDIO_DIR}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
