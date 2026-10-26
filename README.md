# OnTrack

Free, local, realtime transcription for lectures, meetings and in-person conversations on macOS — with a Spotlight-style overlay, live follow-up questions and end-of-session summaries saved to notes.

- **Transcription:** Whisper (mlx-whisper) on Apple Silicon — offline, no quotas. Languages: English, Dutch, Ukrainian (mixed in one session is fine).
- **Speakers:** live `Me` / `Other` (your voiceprint) and `Them` (call audio); after the session `Other` becomes `Speaker A/B/C` (pyannote).
- **Questions & summaries:** Claude via the Claude Code CLI (`claude -p`) on your own Claude subscription.
- **Privacy:** audio never leaves the Mac. After setup the engine runs fully offline; only questions/summaries send transcript text to Claude.

## Install (fresh Mac, ~30 min, mostly downloads)

### 0. What you need

- A Mac with **Apple Silicon** (M1 or newer) and **macOS 14 Sonoma** or newer. Intel Macs are not supported.
- **~10 GB free disk** (models ~5 GB, Python packages ~2 GB).
- A free **Hugging Face** account (step 2) — needed once, to download the speaker models.
- Optional: a **Claude** subscription (Pro or Max) for questions and summaries. Without it, transcription still works.
- Xcode is **not** needed.

### 1. Command-line tools

Run these in Terminal one at a time (paste them without any `#` comments — zsh treats `#` literally):

```bash
xcode-select --install
```
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Open a **new** Terminal window (so `uv` and `claude` are on your PATH), then log in to Claude once:

```bash
claude
```

(Inside Claude, follow the login prompt, then type `/exit`.)

### 2. Hugging Face (free) — do this BEFORE setup

The speaker-separation models are free but "gated": Hugging Face only lets you download them after you accept their terms. Setup checks this first and stops with links if anything is missing, but doing it now saves a round-trip.

1. Create an account at <https://huggingface.co/join> and **confirm your email** (gated models refuse unconfirmed accounts).
2. While logged in, open each of these 4 pages, fill in the short form (any company/website text is fine) and click **Agree and access repository**:
   - <https://huggingface.co/pyannote/segmentation-3.0>
   - <https://huggingface.co/pyannote/speaker-diarization-3.1>
   - <https://huggingface.co/pyannote/speaker-diarization-community-1>
   - <https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM>
3. Create a token at <https://huggingface.co/settings/tokens> → **Create new token** → token type **Read** → copy it (starts with `hf_`).
   - Use **Read**, not *Fine-grained*. Fine-grained tokens only work if you also tick *Read access to contents of all public gated repos you can access*.

The token is only used to download once. After setup, OnTrack never contacts Hugging Face again (you can even delete the token).

### 3. Get the code and run setup

The app expects the repo at `~/Desktop/Work/OnTrack`:

```bash
mkdir -p ~/Desktop/Work
```
```bash
git clone git@github.com:dzhusjr/OnTrack.git ~/Desktop/Work/OnTrack
```
```bash
cd ~/Desktop/Work/OnTrack
```
```bash
scripts/dev.sh all
```

What `all` does, in order:

1. **Checks prerequisites** (Apple Silicon, macOS version, tools, disk space, repo location).
2. **Installs the engine's Python packages.**
3. **Hugging Face login** — paste your Read token when asked (answer `n` to the git-credential question).
4. **Checks access to the 4 gated models** — if any is missing it prints the exact link and stops *before downloading anything*.
5. **Downloads all models** (one time, resumable — rerun `scripts/dev.sh setup` if your connection drops), then verifies the offline cache.
6. **Creates a local code-signing certificate** — macOS asks for your login password once. This makes macOS remember the app's permissions across rebuilds.
7. **Runs the tests, builds the app** into `~/Applications/OnTrack.app` and opens it.

If you cloned somewhere else, tell the app once:

```bash
defaults write com.romandzhus.ontrack engineDir "$PWD/engine"
```

### 4. First launch

1. OnTrack lives in the **menu bar** (no Dock icon). The first launch loads the speech model (~30 s).
2. Allow **Microphone** when asked.
3. Allow **Screen & System Audio Recording** (System Settings → Privacy & Security). OnTrack never records your screen — macOS uses this one permission for *system audio*, which is how it hears the other side of Zoom/Meet/Teams calls. Quit and reopen OnTrack after granting it.
4. Menu bar icon → **Enroll My Voice…** and talk normally for 30 s. This is how your lines get labelled `Me`.

Check everything at any time with:

```bash
scripts/dev.sh doctor
```

### Models it uses

All models run locally. Downloaded once by setup into `~/.cache/huggingface`:

| Model | Used for | Size | Access |
|---|---|---|---|
| `mlx-community/whisper-large-v3-turbo` | live transcription | ~1.6 GB | public |
| `mlx-community/whisper-large-v3-mlx` | saved transcript (re-transcribed at the end, most accurate) | ~3.1 GB | public |
| `mlx-community/whisper-small-mlx` | "fast mode" when live falls behind | ~0.5 GB | public |
| `pyannote/segmentation-3.0` | speaker separation | small | gated (accept terms) |
| `pyannote/speaker-diarization-3.1` | speaker separation pipeline | small | gated (accept terms) |
| `pyannote/speaker-diarization-community-1` | speaker clustering weights | small | gated (accept terms) |
| `pyannote/wespeaker-voxceleb-resnet34-LM` | voiceprints (`Me` vs `Other`) | small | gated (accept terms) |
| Silero VAD | detecting speech | tiny | bundled with the Python package |

If you change a model in the engine config, run `scripts/dev.sh models` once to fetch it.

## Using it

| Action | How |
|---|---|
| Start / stop a session | ⌥⇧S (instant) or menu → Start Session… (asks for a name) |
| Show / hide overlay | ⌥Space (or the eye icon on the bar) |
| Pause / resume recording | ⌥⇧P. Nothing is recorded or transcribed while paused; the transcript gets a `[recording paused mm:ss]` note |
| Live transcription on/off | ⌥⇧L, the bubble icon on the bar, or menu → Live Transcription. Off = record only; transcribed when you save |
| Save battery | menu → Transcribe After Recording Only When Plugged In: on battery, Save keeps the audio and the heavy work (speaker separation, large-v3, summary) runs once you plug in |
| Ask about the session | ⌥Space → type → Enter (Esc to collapse). Answers are kept to a few lines |
| Someone says your name | the line gets a 👋 marker, the bar pops up, and a 1–2 line suggested reply appears. Menu → Names & Trigger Words… sets which words count (name, nicknames, anything); menu → Auto-Suggest Replies turns the drafted reply off (lines are still highlighted) |
| Interview / meeting helper | menu → Interview Mode: a short suggested reply for every question someone asks you. ⌥⇧A drafts one for the latest question anytime |
| Work inside a project | menu → Project → Choose Folder… (e.g. your CETL repo). Questions and suggested replies then use that folder (read-only) and its CLAUDE.md as context. Project → Also Save Notes Into This Folder copies each saved session to `<project>/OnTrack Notes` |
| Speed | live questions and suggested replies use the fastest Claude model (`fast_llm_model = "haiku"`); the end-of-session summary uses `llm_model` (Sonnet) |
| Whole conversation in one language | menu → Language → Українська / English / Nederlands. Most accurate; stops short phrases being "translated" into English |
| End of session | overlay card: **Save summary + transcript** (Enter) · Transcript only · Discard (click twice) |

Files: live sessions in `sessions/`, saved notes in `notes/<session>/` (summary.md, transcript.md, qa.md, notes.md). Menu → Also Save Notes To… copies every saved session into a folder of your choice (e.g. iCloud Drive).

Engine settings: menu → Edit Engine Config… (`~/Library/Application Support/OnTrack/config.toml`). Logs: menu → Open Logs.

## Troubleshooting

- **`403 Forbidden` / "Cannot access gated repo" / "Not accepted yet"** — you haven't accepted the terms of one of the 4 pyannote models, or you're logged in with a different HF account than the one that accepted. Open the link printed by setup, accept, rerun `scripts/dev.sh setup`. `scripts/dev.sh doctor` shows which account you're logged in as.
- **"token was rejected"** — the token was deleted or is not a Read token. Make a new Read token, then `cd engine` and `uv run hf auth login --force`.
- **`command not found: uv` / `claude`** — open a new Terminal window after installing (they're added to your PATH on new shells).
- **`zsh: command not found: #`** — you pasted a line with a `#` comment; paste the command without it.
- **Xcode / "XCTest not found"** — not needed; the build uses the Command Line Tools. Swift unit tests are skipped without full Xcode, that's fine.
- **macOS asks for Microphone / Screen Recording again after every rebuild** — run `scripts/dev.sh cert` once, then `scripts/dev.sh app`. If it's still stuck: `scripts/dev.sh reset-permissions`, reopen the app and grant again.
- **Call audio shows up twice (as `Them` and `Other`) or the live text is odd on calls** — that's the call coming out of the speakers into the mic. OnTrack now drops mic speech that overlaps call audio (unless it's your own voice), but headphones still give the cleanest result. Voice Isolation doesn't help here (and would mute people in the room).
- **Ukrainian/Dutch lines come out in English** — set menu → Language to the conversation's language.
- **Nothing is transcribed, files are empty** — the mic delivers silence: check System Settings → Sound → Input, and that OnTrack is allowed under Privacy & Security → Microphone.
- **"No voiceprint yet"** — menu → Enroll My Voice… (talk for 30 s in a normal voice).
- **"Claude Code CLI not found" / summaries fail** — install and log in (step 1), then menu → Retry Summary. Transcripts are saved either way.
- **The app opens but nothing happens** — check `scripts/dev.sh doctor`; most often the repo is not at `~/Desktop/Work/OnTrack` (see step 3).
- Still stuck: `scripts/diag.sh` prints the logs and settings to share.

## Development

```bash
scripts/dev.sh test
```
```bash
cd engine && uv run pytest -m models -v
```
```bash
cd engine && uv run pytest -m soak -s
```

`test` runs the engine unit tests and Swift core tests; `-m models` runs real-model tests (Mac); `-m soak` a 3 h simulated session. `scripts/dev.sh app` rebuilds the app. Design: `docs/superpowers/specs/2026-10-23-ontrack-design.md` · Plans: `docs/superpowers/plans/` · Manual checklist: `docs/acceptance.md`.
