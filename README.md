# OnTrack

Free, local, realtime transcription for lectures, meetings and in-person conversations on macOS — with a Spotlight-style overlay, live follow-up questions and end-of-session summaries saved to notes.

- **Transcription:** Whisper large-v3-turbo (mlx-whisper) on Apple Silicon — offline, no quotas.
- **Speakers:** live `Me` / `Other` (your voiceprint) and `Them` (call audio); after the session `Other` becomes `Speaker A/B/C` (pyannote).
- **Questions & summaries:** Claude via the Claude Code CLI (`claude -p`) on your existing subscription.
- **Design:** `docs/superpowers/specs/2026-10-23-ontrack-design.md` · Plans: `docs/superpowers/plans/`.

## First-time setup (Mac)

```bash
brew install uv xcodegen
npm i -g @anthropic-ai/claude-code && claude      # log in once
cd ~/Desktop/Work/OnTrack
scripts/dev.sh all                                  # engine deps + HF login + tests + build + launch
```

Then:
1. Allow **Microphone** and **Screen & System Audio Recording** for OnTrack when macOS asks (System Settings → Privacy & Security). Quit and reopen OnTrack after granting screen recording.
2. Menu bar icon → **Enroll My Voice…** and talk for 30 s.

> The app is ad-hoc signed, so macOS may ask for permissions again after each rebuild. To avoid that, open `app/OnTrack.xcodeproj`, select your Apple ID team under *Signing & Capabilities*, and build from Xcode.

## Using it

| Action | How |
|---|---|
| Start / stop a session | ⌥⇧S (instant) or menu → Start Session… (asks for a name) |
| Show / hide overlay | ⌥Space (or the eye icon on the bar) |
| Live transcription on/off | ⌥⇧L, the bubble icon on the bar, or menu → Live Transcription. Off = record only; transcribed when you save |
| Save battery | menu → On Battery: Record Only |
| Ask about the session | ⌥Space → type → Enter (Esc to collapse) |
| End of session | overlay card: **Save summary + transcript** (Enter) · Transcript only · Discard (click twice) |

Files: live sessions in `sessions/`, saved notes in `notes/<session>/` (summary.md, transcript.md, qa.md, notes.md). A daily Cowork task copies new notes into the "OnTrack" Claude Project. You can also ask Claude in Cowork about a running session — it reads `sessions/<id>/transcript.md`.

Engine settings: menu → Edit Engine Config… (`~/Library/Application Support/OnTrack/config.toml`). Logs: menu → Open Logs.

## Development

```bash
scripts/dev.sh test        # engine unit tests + Swift core tests
cd engine && uv run pytest -m models -v            # real-model tests (Mac)
cd engine && uv run pytest -m soak -s              # 3 h simulated soak
scripts/dev.sh app         # regenerate Xcode project and build the app
```

Manual acceptance checklist: `docs/acceptance.md`.
