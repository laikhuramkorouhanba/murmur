# Murmur

> **[PROBLEM STORY PLACEHOLDER — The project owner will write this story.]**

## What Murmur does

Murmur is a macOS menu bar companion for blind and low-vision developers. It watches the selected tab in Terminal.app, cleans command output, and speaks the useful parts. Quick successes stay quiet. Longer successes get a sound or a short message. Errors get a spoken summary, with the exact output still available in Terminal.

Murmur can also ask Codex to fix the most recent failure in its Git project. The change is shown, spoken, and available to undo.

## 90-second demo

Codex response time varies, so the walkthrough may run longer than 90 seconds.

1. **0:00** — Start Murmur with `uv run python -m murmur.app`.
2. **0:10** — Open a **new Terminal window**. Go to the Murmur checkout with `cd /path/to/murmur`, then run `bash demo/make_fixme_project.sh`. It recreates `~/murmur-fixme-demo` and gives that folder its own Git repository.
3. **0:25** — Run the failing pytest command printed by the script. Murmur announces the shop test failure.
4. **0:35** — Press **⌘⌥⇧F**. Murmur asks Codex for a small fix and speaks what changed.
5. **1:10** — Choose **Open last fix diff**, then **Undo last fix** from the Murmur menu. Run the test again to see the original failure return.

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

## Hotkeys

- **⌘⌥⇧M** reads the latest Terminal output. Press it again to stop speech.
- Tap **Right Command** twice to read the output. The menu lets you choose Right Option or Right Shift instead. Repeat the double tap to stop speech.
- **⌘⌥⇧F** fixes the latest failure. Press it again while a fix is running to cancel and roll back its changes.

Automatic announcements are on by default. Turn **Auto-announce finished commands** off in the menu to pause them.

## Personalities and voices

- **Smooth** is the default. It adds a little warmth and varies its short phrases.
- **Focused** is brief and plain.
- **Marin** is the default OpenAI voice. **Cedar** is also available.

Murmur remembers these choices in `~/.murmur/settings.json`. OpenAI speech falls back to macOS `say` if the key, network, or speech service is unavailable.

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

Short successful commands can skip the summariser and stay silent or play a soft sound.

## Honest limits

- Murmur watches only the selected tab in Terminal.app's front window. VS Code terminals are not supported yet.
- It polls about once a second, so commands that finish very quickly can be missed. It judges success from terminal text, not an exit code.
- Text alone cannot always tell a Python REPL from a script. Stopping a server with Ctrl-C can look like a failure.
- OpenAI summaries and speech need a key and network access. Local summary and macOS speech are fallbacks, not equivalent voices.
- Fix it only works in a Git project and may not find a safe fix. Undo covers the files in its snapshot diff, not an unlimited project history.
- Murmur is not packaged as a standalone `.app` yet.

## What's next

- Add VS Code terminal support.
- Let Murmur speak through VoiceOver so it can use the user's chosen voice and speed.
- Add spoken follow-up questions such as “What went wrong?” and “Read the full error.”
