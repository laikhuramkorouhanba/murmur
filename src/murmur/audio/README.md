# Pre-rendered audio

Pre-rendered OpenAI audio for Murmur's scripted lines: the Natural voice Marin, in
Brief style, at each of the four speeds (Normal, Fast, Very fast, Maximum). Each
file is named after a hash of the exact words, voice, style and speed, so a
changed line never plays an old recording.

VoiceOver and the Mac voice work offline anyway, so they don't need these files.

It ships inside Murmur.app (see `packaging/Murmur.spec`), and the code finds it at
`config.AUDIO_DIR`, both with `uv run` and in the built app. Re-render with
`uv run python -m murmur.prerender --prune`.
