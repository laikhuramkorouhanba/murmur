# Murmur

When a blind developer runs a command, their screen reader reads every line of output, including progress bars, colour codes and long stack traces. The line that matters can be buried. I built Murmur so they hear what matters first in one short sentence. They can fix the error with Codex in one shortcut.

Demo video: (link coming)

**Built with:** Python, rumps, OpenAI (`gpt-4o-mini-tts`, Marin voice), and Codex CLI for Fix it.

## What Murmur does

Murmur is a macOS menu bar companion for blind and low-vision developers. It watches the selected tab in Terminal.app, cleans command output, and speaks the useful parts, briefly, because screen reader users often listen at two to three times normal speed:

- An error: a fail sound, then "Failed. shop_demo_check.py, line 9. Expected 21.60, got 18.40." Status first, then the file, line and exact cause, nothing else.
- Short successful output (about 10 lines or less, like `ls`): a pass sound, then the output word for word.
- Longer successful output: a pass sound, then "Done, 42 lines."
- No output: silence.

It speaks with the fast, offline **Mac voice** (or OpenAI's **Natural voice**, Marin). When VoiceOver is running, auto-announce plays only the pass or fail sound, so Murmur never talks over VoiceOver; double tap Right Command, then Space, to hear Murmur's words. The exact output is always one key away.

Murmur can also ask Codex to fix the most recent failure in its Git project. The change is shown, spoken, and available to undo.

## 90-second demo

Codex response time varies, so the walkthrough may run longer than 90 seconds.

1. **0:00** — Start Murmur with `uv run python -m murmur.app`.
2. **0:10** — Open a **new Terminal window**. Go to the Murmur checkout with `cd /path/to/murmur`, then run `bash demo/make_fixme_project.sh`. It recreates `~/murmur-fixme-demo` and gives that folder its own Git repository.
3. **0:25** — Run the failing pytest command printed by the script. Murmur announces the shop test failure.
4. **0:35** — Double tap **Right Command**, then press **F** (or press **⌘⌥⇧F**). Murmur asks Codex for a small fix and speaks what changed.
5. **1:10** — Double tap **Right Command**, then press **U** (or choose **Undo last fix** from the menu). Run the test again to see the original failure return.

## Install and run

Murmur currently runs from source on macOS. It needs Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```sh
brew install portaudio
uv sync
cp .env.example .env
```

Add your `OPENAI_API_KEY` to `.env` if you want OpenAI summaries and speech. Keep that file private; it is ignored by Git. Without a key, Murmur uses its local error summary and the built-in macOS voice.

Start the app:

```sh
uv run python -m murmur.app
```

On first use, allow the app that launched Murmur (usually Terminal) to control Terminal in **System Settings → Privacy & Security → Automation**. Enable that same app under **Accessibility** and **Input Monitoring** for global shortcuts. Murmur reports permissions it cannot use.

Fix it also needs the Codex CLI installed and logged in:

```sh
brew install codex
codex login
```

### Build Murmur.app

```sh
./build_app.sh
```

This makes `dist/Murmur.app` with PyInstaller. It runs without a terminal, lives only in the menu bar (no Dock icon), and writes its log to `~/Library/Logs/Murmur/murmur.log` (**Show log** in the menu). The first time it starts without a key, it asks for your OpenAI API key and keeps it in the macOS Keychain; change it later with **Set OpenAI API key…**. The app never reads `.env`. It is not signed or notarized, so the first time, right-click it and choose **Open**. macOS asks for permissions for Murmur itself, and after each rebuild you may need to turn Murmur off and on again under Accessibility and Input Monitoring.

### Demo mode and the stage safety net

- **Demo → Demo mode** (menu): replays a real recorded run with no Terminal, internet or Codex needed. It turns captions on and says up front that it is a replay. Then it announces the recorded failure and replays the recorded fix, applying the same one-line change to `~/murmur-fixme-demo` through the normal Fix it code, so **Undo last fix** works afterwards (the diff is saved at `~/.murmur/last_fix.diff`). Any Murmur shortcut stops it.
- **Demo → Reset demo project** (menu): puts the demo's bug back and makes Murmur forget the old failure and fix. The builder only ever deletes a folder that holds its `.murmur-demo` marker, so a real folder that happens to be called `murmur-fixme-demo` is never touched.
- **Pre-rendered speech:** for the Natural voice, every scripted line is recorded once in Marin, Brief style, at the default speed, in `src/murmur/audio/` (14 lines, about 1.5 MB). That covers Demo mode's whole script, the Fix it cues, the "Murmur" key layer cue and the welcome. With the Natural voice, speaking tries a recording first, then live OpenAI speech, then the macOS voice. Each file is named after a hash of the model, voice, style, speed and exact words, so a changed line is never played from an old recording. At any other speed the line is spoken live. The Mac voice works offline anyway.
  - `uv run python -m murmur.prerender --check` lists any missing lines.
  - `uv run python -m murmur.prerender --prune` renders them and removes unused files.
- **The recording:** `demo/recorded/` holds one real run (the failing output, the fix's answer and its exact diff). Make a new one with `uv run python demo/record_demo.py`. That calls Codex once.
- **Rehearse offline:** `uv run python -m murmur.selftest --no-codex --muted --demo-mode` adds a Demo mode run with every OpenAI request blocked. It checks that every line came from a recording, then checks Undo and Reset, and that the macOS voice takes over when both a recording and the network are missing.

### Self-test

```sh
uv run python -m murmur.selftest --no-codex --muted
```

Runs Murmur's real pipeline end to end, without the menu bar. It opens its own Terminal window (it never touches yours) with the virtual env active, then:
- rebuilds the demo and runs the failing test;
- waits for auto-announce and checks it played the fail sound, then said exactly "Failed. shop demo check dot py, line 9. Expected 21.60, got 18.40." (Brief wording);
- runs Fix it the way ⌘⌥⇧F does and checks only `shop.py` changed;
- checks the test passes, undoes the fix, and checks the test fails again;
- runs `ls` and checks the pass sound played and the file names were read word for word;
- checks the key layer's key-swallowing tap with F19 and F18 (keys that do nothing in your apps): F19 must be held back from the app behind it while the layer is open, and F18 must pass.

It uses Brief style and "Errors and short output" while it runs, then puts your settings file back exactly as it was.

Then it closes its window and prints a table of every step: PASS or FAIL, the time taken, what was spoken, the voice engine, and the first-audio time. The exit code is 0 only if every step passes.

- `--muted` (the default): no sound, but speech is still requested from OpenAI, so the timings are real.
- `--speak`: play the audio so you can listen.
- `--no-codex`: apply the known one-line fix instead of calling Codex, to save quota.

The full log is in `~/.murmur/selftest.log`.

## Keys

**The Murmur keys.** Double tap **Right Command**. Murmur says "Murmur" and waits about 3 seconds for one key:

| Key | Does |
| --- | --- |
| Space | Repeat the last announcement |
| R | Read the full output, word for word |
| F | Fix the last error with Codex (press again to cancel) |
| U | Undo the last fix |
| Escape | Cancel and stop speech |
| Up / Down arrow | One step faster / slower, said as "Faster" or "Slower" at the new speed (press as often as you like) |

That key never reaches Terminal. Any other key closes the layer and is typed as normal. How it's kept from Terminal: Murmur makes a key-swallowing event tap at startup but keeps it switched off. It's switched on only while the layer waits for its key, so the rest of the time your typing never waits for Murmur.

**Control** on its own stops speech, like VoiceOver. During a fix it only quiets the current line; the fix keeps going.

**Backups:** **⌘⌥⇧M** reads the latest output, **⌘⌥⇧F** fixes, **⌘⌥⇧R** reads the full output. File places such as `shop.py:9:5` are read as "shop.py, line 9, column 5".

## Auto-announce

**Auto-announce** in the menu is a single on/off switch (on by default):

| Finished command | Murmur does |
| --- | --- |
| An error | fail sound, then "Failed. file, line, exact cause." |
| Up to about 10 lines of output | pass sound, then the output word for word |
| Longer output | pass sound, then "Done, 42 lines." |
| No output | silent ("Done." if it ran 15 s or more) |
| Anything, while VoiceOver is running | just the pass or fail sound |

The pass sound is two quick notes going up; the fail sound is two lower notes going down. One plays before any announcement.

## Voice, speed and style

- **Voice:** **Mac voice** (the default: fast and offline) or **Natural voice** (OpenAI's Marin, needs a key and the internet).
- **Speed:** a slider in the menu (VoiceOver can adjust it: it's named "Speed" and says its value as a percentage). When you let go, Murmur says a short sample at the new speed. It maps onto each voice's real range: the Mac voice from 150 to 420 words a minute (it stops getting faster at about 400), the Natural voice from OpenAI speed 0.8 to 3.0.
- **Style:** Brief only: the status word first, then the file, line and exact cause.
- **Pronunciation:** before any speech, names are rewritten so they sound the same every time: README → "read me", `.py` → "dot pie", `.md` → "dot M D", underscores → spaces, plus common ones like `pyproject`, `pytest`, `.json`, `.toml` and CamelCase error names. Captions keep the real names.

Murmur remembers these choices in `~/.murmur/settings.json`. If the speakers fail (for example PortAudio error -9986 after headphones change), Murmur restarts audio and tries once more, then uses the Mac voice.

## How Murmur uses Codex

Fix it sends the last failure to `codex exec`. The command is built with these flags:

```sh
codex exec \
  --cd <project-root> \
  --sandbox workspace-write \
  -c 'approval_policy="never"' \
  -c 'sandbox_workspace_write.network_access=false' \
  -c 'model_reasoning_effort="low"' \
  --ephemeral \
  --color never \
  --output-schema <temporary-answer-schema.json> \
  -o <temporary-answer.json> \
  <prompt>
```

- `--cd` selects the Git project where the failed command ran.
- `--sandbox workspace-write` confines Codex's work to that project workspace.
- `approval_policy="never"` prevents approval prompts; an action that needs approval fails instead.
- `sandbox_workspace_write.network_access=false` blocks network access. The prompt also tells Codex not to install packages or commit.
- `model_reasoning_effort="low"` keeps the fix quick.
- `--ephemeral` avoids keeping a Codex session.
- `--color never` keeps captured output plain.
- `--output-schema` requires a small JSON answer with `fixed`, `summary`, and `verified` fields. `-o` writes that answer to a temporary file.
- The prompt asks for the smallest safe change and permits one test rerun when appropriate. Murmur sends the failure text to Codex through standard input.

Before and after Codex runs, Murmur records Git tree snapshots using a private temporary index. This does not change your staged files or create a commit. Undo restores the files changed by the fix, removes files the fix added, and refuses if one of those files has changed since. Undo is available during the current Murmur session; the exact diff is saved at `~/.murmur/last_fix.diff`.

Fix it refuses to run outside a Git project, in `/`, or with your whole home folder as the project. Codex cannot use the network or install packages. A fix is not guaranteed; if Codex cannot make a safe change, Murmur reports that and rolls back its partial edits.

## Architecture

```text
Terminal.app → Watcher → Cleaner → Summariser → Voice
                  │          │          │           └─ OpenAI speech or macOS say
                  │          │          └─ errors and long output only
                  │          └─ removes terminal decoration
                  └─ busy flag and scrollback

Fix shortcut → Fixer → Git snapshots → codex exec → diff, spoken summary, Undo
```

Brief style names the exact cause from the output itself, so most failures need no summary request at all. The summariser is only asked when the output doesn't name a cause.

## Honest limits

- Murmur watches only the selected tab in Terminal.app's front window. VS Code terminals are not supported yet.
- It judges success from terminal text, not an exit code.
- Murmur doesn't speak through VoiceOver itself (announcements from a menu bar app weren't spoken in a real test), so with VoiceOver on, auto-announce is sounds only.
- Text alone cannot always tell a Python REPL from a script. Stopping a server with Ctrl-C can look like a failure.
- OpenAI summaries and speech need a key and network access. Local summary and macOS speech are fallbacks, not equivalent voices.
- Fix it only works in a Git project and may not find a safe fix. Undo covers the files in its snapshot diff, not an unlimited project history.
- Murmur.app is built locally and is not signed. Demo mode and Reset demo project need the source folder for now.

## Development

Built over one weekend with Codex as the main coding agent, a shared project brief and a running handoff log, both kept locally.

## What's next

- Add VS Code terminal support.
- Add spoken follow-up questions such as “What went wrong?” and “Read the full error.”
