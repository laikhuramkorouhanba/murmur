# Pre-rendered audio

This folder is where M7's pre-rendered OpenAI audio files go (Marin voice, Smooth
personality). Each scripted line gets a WAV file named after a hash of its exact words.

It ships inside Murmur.app (see `packaging/Murmur.spec`), and the code finds it at
`config.AUDIO_DIR`, both with `uv run` and in the built app.
