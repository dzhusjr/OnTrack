# OnTrack — Design Spec

- **Date:** 2026-10-23
- **Status:** Draft — awaiting review
- **Owner:** Roman
- **Process:** Spec-driven (Superpowers): spec → implementation plan → TDD execution

## 1. Purpose

A Granola-style, **completely free**, **realtime**, **reliable** transcriber for macOS that works for **online calls and in-person conversations/lectures**. While a session runs, Roman sees the last few lines of the live transcript in a minimal floating overlay and can ask follow-up questions in that overlay. When the session ends, OnTrack offers to save a summary and the raw transcript to his notes, which are synced to the "OnTrack" Claude Project.

### Success criteria

1. **Latency:** p90 ≤ 3 s from the end of an utterance to its line appearing in the overlay.
2. **Cost:** €0 recurring. No paid APIs. Uses local models plus Roman's existing Claude subscription (via Claude Code CLI).
3. **Reliability:** a 3-hour session completes without data loss; if the engine crashes, the app restarts it within 5 s, continues the same session, and every line written before the crash is preserved.
4. **Languages:** English, Russian, Ukrainian, including switching mid-session (detected per utterance).
5. **Speakers:** live `Me` / `Other` (in person) and `Me` / `Them` (calls); after the session, `Other` is split into `Speaker A/B/C`. Target ≥ 90 % correct Me-vs-Other on a test conversation.
6. **Unobtrusive UI:** overlay never steals focus, shows ≤ 3 lines collapsed, translucent, minimal.

### Non-goals (v1)

Auto-start (call or calendar detection), live A/B/C diarization, in-app local LLM, cloud sync of audio, iOS/phone capture, multi-user, Windows/Linux.

## 2. Decisions log

| # | Decision | Reason |
|---|---|---|
| D1 | Local transcription, `whisper large-v3-turbo` via **mlx-whisper** on Apple Silicon (M5, 16 GB) | Free, offline, no quotas; MLX is native to Apple Silicon and pure-Python to integrate. Hidden behind a `Transcriber` interface so whisper.cpp can replace it if benchmarks favour it. |
| D2 | Audio sources: Mac mic + system audio | In-person and online. |
| D3 | System audio via **ScreenCaptureKit**, mic via **AVAudioEngine** with Apple voice processing (AEC) enabled | No BlackHole/virtual devices; volume keys keep working; AEC reduces speaker echo into the mic. |
| D4 | Speaker labels: channel-based (`Them` = system audio) + **voiceprint** match on mic (`Me` vs `Other`); offline pyannote diarization for A/B/C | Speaker verification against one enrolled voice is reliable live; full clustering is only reliable on the whole recording. |
| D5 | Architecture A: **SwiftUI app** (capture, menu bar, hotkey, overlay) + **Python engine** (VAD, ASR, speaker ID, storage, Q&A, post-processing) | Python keeps most logic testable and in Roman's main stack; Swift is used only where macOS APIs require it. |
| D6 | LLM = **Claude via Claude Code CLI** (`claude -p`), headless, no tools | Uses existing subscription → free; best quality incl. Russian. |
| D7 | Sessions start/stop **manually** (menu bar or global hotkey) | Predictable; nothing is recorded by accident. |
| D8 | On stop, overlay prompts: save summary + transcript / transcript only / discard | Requested by Roman. |
| D9 | Raw audio is temporary: deleted after post-processing (or on discard) | Privacy, disk usage. |

## 3. Architecture

```
┌────────────────────────── OnTrack.app (SwiftUI) ──────────────────────────┐
│ MenuBarController   HotkeyService (⌥Space)   OverlayPanel (NSPanel)       │
│ CaptureService ── mic (AVAudioEngine+AEC) ─┐                              │
│                └─ system (ScreenCaptureKit)┤                              │
│ EngineSupervisor (spawns/restarts engine)   │                              │
│ EngineClient ───── audio.sock (binary) ◄────┘                              │
│              └──── control.sock (NDJSON, bidirectional) ◄───────────────┐  │
└──────────────────────────────────────────────────────────────────────────┼──┘
                                                                           │
┌──────────────────────────── ontrack engine (Python) ─────────────────────┼──┐
│ AudioSource → Segmenter(VAD, per channel) → Transcriber(mlx-whisper)      │  │
│                                     └→ SpeakerLabeler(voiceprint)         │  │
│ EchoFilter → SessionWriter(transcript.md/.jsonl) → ControlServer ─────────┘  │
│ QAService(claude -p)   PostProcessor(pyannote + re-ASR)   Summarizer(claude) │
└──────────────────────────────────────────────────────────────────────────────┘
        │ writes
        ▼
  ~/Desktop/Work/OnTrack/sessions/<id>/   (live, readable by Claude in Cowork)
  ~/Desktop/Work/OnTrack/notes/<id>/      (saved summary + transcript)
  ~/Library/Application Support/OnTrack/  (sockets, temp audio, voiceprint, logs, models)
```

### 3.1 Repository layout

```
OnTrack/
  app/                      Xcode project "OnTrack" (SwiftUI, macOS 15+)
    OnTrack/                Capture, Overlay, MenuBar, Hotkey, EngineClient, Supervisor
    OnTrackTests/           XCTest
  engine/                   Python package (uv-managed, Python 3.12)
    pyproject.toml
    src/ontrack/            audio_source, segmenter, transcriber, speaker, echo,
                            session, control, qa, summarize, postprocess, enroll,
                            config, engine (entrypoint), cli
    tests/                  pytest (unit with fakes; integration marked `models`)
    tests/fixtures/         short WAV clips (en/ru/uk, two-voice conversation)
  docs/superpowers/specs/   this file
  docs/superpowers/plans/   implementation plan(s)
  sessions/                 gitignored — live session folders
  notes/                    gitignored — saved summaries/transcripts
```

## 4. Components

### 4.1 Swift app (`app/`)

- **CaptureService** — produces two streams, each resampled to 16 kHz mono float32, in 20 ms frames.
  - Mic: `AVAudioEngine.inputNode` with `setVoiceProcessingEnabled(true)` (echo cancellation + noise suppression).
  - System: `SCStream` with `capturesAudio = true`, `excludesCurrentProcessAudio = true`, minimal 2×2 px video config (required by API, discarded).
  - Emits JSON permission/readiness events; never crashes on permission denial — reports `error{code:"perm_mic"|"perm_screen"}`.
- **EngineClient** — writes audio frames to `audio.sock`, exchanges NDJSON on `control.sock`. Reconnects with backoff (100 ms → 2 s). Buffers up to 10 s of audio while reconnecting so nothing is lost across an engine restart.
- **EngineSupervisor** — launches `engine/.venv/bin/ontrack-engine`, health-checks via `ping` every 2 s, restarts on exit or 3 missed pings. Passes the current session id so the engine resumes the same session.
- **HotkeyService** — global hotkeys (default ⌥Space toggles overlay, ⌥⇧S start/stop session). Configurable.
- **MenuBarController** — icon states: idle / recording (red dot) / processing; menu: Start/Stop, session name, Enroll voice, Open sessions folder, Quit.
- **OverlayPanel** — see §6.

### 4.2 Python engine (`engine/src/ontrack/`)

Each unit has one job and a small interface; heavy dependencies sit behind protocols so tests use fakes.

| Module | Interface | Notes |
|---|---|---|
| `audio_source` | `iter_frames() -> Iterator[AudioFrame(channel, t_ns, samples)]` | `SocketAudioSource` (prod), `WavAudioSource` (tests). Validates framing; drops malformed frames with a counter. |
| `segmenter` | `push(frame) -> list[Utterance]`, `flush()` | Silero VAD per channel. Speech start pad 200 ms, end after 600 ms silence. Max utterance 15 s, split at the lowest-energy 100 ms window in the last 3 s. |
| `transcriber` | `transcribe(u) -> Transcript(text, lang, avg_logprob, no_speech_prob)` | `MlxWhisperTranscriber` (large-v3-turbo). Language: auto-detect restricted to {en, ru, uk}; if detected lang ∉ set, re-run with top allowed. Previous 1–2 lines of same channel passed as `initial_prompt` for continuity. |
| `hallucination` | `is_hallucination(t, u) -> bool` | Reject if `no_speech_prob > 0.6 and avg_logprob < -1.0`, compression ratio > 2.4, or text in a known-phrase blocklist ("Thanks for watching", "Продолжение следует", …). |
| `speaker` | `label(u) -> ME|OTHER|THEM` | channel 1 → `THEM`. Channel 0 → cosine similarity of pyannote/wespeaker embedding vs enrolled voiceprint; ≥ τ (default 0.55, tunable) → `ME`, else `OTHER`. No voiceprint → `ME` for all mic audio, and a one-time hint to enroll. Utterances < 1 s use the label of the neighbouring utterance on the same channel if within 1.5 s. |
| `echo` | `filter(line, recent_them) -> bool` | Drops mic lines that duplicate a `THEM` line overlapping in time (token-set similarity ≥ 0.8) — safety net for laptop-speaker calls when AEC is insufficient. |
| `session` | `SessionWriter.start(name)`, `append(line)`, `close()` | Writes `transcript.jsonl` (source of truth, one JSON line per utterance, flushed + fsync) and regenerates `transcript.md` incrementally. Resumable: on restart with an existing id, reopens and continues. Raw audio per channel → `~/Library/Application Support/OnTrack/audio/<id>/{mic,system}.flac` (chunked, 60 s files). |
| `control` | NDJSON server | Message schema in §5. |
| `qa` | `ask(question, session) -> AsyncIterator[str]` | Builds prompt (system instructions + full transcript so far + Roman's `notes.md` + question), runs `claude -p --output-format stream-json`, no tools, timeout 60 s, one in-flight question (others queued). Answers appended to `session/qa.md`. |
| `postprocess` | `run(session_id) -> FinalTranscript` | pyannote `speaker-diarization-3.1` on mic audio; maps clusters → `Me` (best voiceprint match) / `Speaker A/B/C`; re-transcribes with large-v3-turbo full precision at segment boundaries; writes `transcript.final.md`. |
| `summarize` | `summarize(final_transcript, notes) -> str` | `claude -p` with a summary template (TL;DR, key points, decisions, action items with owner, open questions, glossary of terms). Output in the session's dominant language unless overridden. |
| `enroll` | `ontrack enroll` | Records 30 s of Roman reading a prompt (mic only), computes mean embedding over 3+ windows, saves `voiceprint.npy`; prints self-similarity sanity score. |
| `config` | TOML at `~/Library/Application Support/OnTrack/config.toml` | Paths, hotkeys, τ, model ids, overlay line count, LLM model. |

## 5. IPC protocol

**`audio.sock`** (app → engine), little-endian frames:
`magic:u16=0x4F54 | channel:u8 (0 mic, 1 system) | flags:u8 | t_ns:u64 (monotonic) | n:u32 | n × float32`

**`control.sock`** — newline-delimited JSON, every message has `type`; requests carry `id`.

App → engine: `ping`, `start_session{name}`, `stop_session`, `ask{id, question}`, `finalize{session_id, action: "save_all"|"transcript_only"|"discard"}`, `enroll_start`, `set_config{...}`.

Engine → app: `pong{state}`, `status{state: idle|recording|stopping|postprocessing, model_ready, session_id}`, `line{session_id, seq, speaker, t_start, t_end, lang, text}`, `answer_delta{id, text}`, `answer_done{id}`, `session_stopped{session_id, duration_s, line_count}`, `finalize_progress{session_id, stage, pct}`, `finalize_done{session_id, notes_path}`, `error{code, message, recoverable}`.

`seq` is monotonic per session so the app can de-duplicate after reconnect.

## 6. Overlay UX

- **Window:** borderless `NSPanel`, `.nonactivatingPanel`, level `.floating`, `collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]`. Material `.hudWindow`/`.ultraThinMaterial`, corner radius 14, subtle 1 px border, soft shadow. Width 560 pt, top-center by default, draggable; position remembered.
- **Collapsed (recording):** last **3** lines (configurable 1–5). Each line: small coloured dot (Me = blue, Other = amber, Them = green), text in SF Pro 13 pt; older lines fade to 50 % opacity; new lines slide in. Tiny elapsed-time + red dot at the right. No scrolling.
- **Expanded (⌥Space or start typing while panel focused):** panel becomes key, shows a single-line question field ("Ask about this session…"). Enter → answer streams below (max 8 lines, scrollable), Esc collapses and returns focus to the previous app. Last Q&A remains viewable until the next question.
- **Hidden:** ⌥Space while expanded with empty field hides the overlay; recording continues (menu bar shows red dot).
- **End-of-session card** (on stop): "Session ended · 47 min · 312 lines" with three buttons — **Save summary + transcript** (default, Enter), **Transcript only**, **Discard** (requires second click). Then a slim progress bar ("Separating speakers… / Re-transcribing… / Summarizing…") and finally "Saved to notes ✓ · Open".
- Respects Reduce Transparency / Reduce Motion.

## 7. Data flow

1. Start → engine creates `sessions/<YYYY-MM-DD-HHMM>-<slug>/` with `meta.json`, empty `notes.md`, begins writing audio.
2. Frames → Segmenter per channel → Utterance → (Transcriber ∥ SpeakerLabeler) → hallucination + echo filters → SessionWriter → `line` to app → overlay.
3. Ask → QAService streams `answer_delta` to overlay; appended to `qa.md`.
4. Stop → flush segmenter, close writer → `session_stopped` → overlay shows end-of-session card.
5. Finalize:
   - `save_all`: postprocess → summarize → write `notes/<id>/{summary.md, transcript.md, qa.md}` → delete audio → mark `notes/<id>/.pending_sync`.
   - `transcript_only`: postprocess → `notes/<id>/transcript.md` → delete audio → `.pending_sync`.
   - `discard`: delete audio and the session folder (after confirmation).
6. **Claude Project sync:** a Cowork scheduled task (daily, plus on demand when Roman asks in chat) reads `notes/*/.pending_sync`, writes each note into the OnTrack Claude Project with `project_write`, then removes the marker. (The Mac app cannot write to claude.ai Projects directly; this keeps it free and reliable.)
7. **Live follow-ups in Cowork:** Claude can also read `sessions/<id>/transcript.md` from the connected folder at any time.

## 8. Error handling & reliability

| Failure | Behaviour |
|---|---|
| Mic / screen-recording permission missing | Overlay + menu show a fix-it message with a button opening the right System Settings pane; session can run with whichever source is available. |
| Engine crash | Supervisor restarts ≤ 5 s; app re-sends `start_session` with the same id; writer resumes; app buffers ≤ 10 s audio meanwhile. Gap > 10 s is recorded as a `[gap 00:12]` marker line. |
| Swift capture stream error (device change, AirPods connect) | CaptureService rebuilds the affected stream within 1 s; `[audio source changed]` marker. |
| ASR falls behind (queue > 20 s audio) | Switch live model to `small` until caught up (post-processing still uses turbo); emit `status.degraded`. |
| `claude` CLI missing / not logged in / timeout / rate limit | Overlay shows a short error; question is stored in `qa.md` as unanswered. Summary step marks `summary_pending` and can be retried from the menu ("Retry summary"); transcript is still saved. |
| pyannote fails or HF token missing | Fall back to live labels (`Me/Other/Them`) in the final transcript; log warning. |
| Disk full | Stop audio writing first, keep transcript; error to overlay. |
| Corrupt/partial `transcript.jsonl` line after crash | Reader ignores an incomplete trailing line. |

Logs: `~/Library/Application Support/OnTrack/logs/{app,engine}.log`, rotating, 10 MB × 5.

## 9. Privacy & permissions

- Everything runs locally; only transcript text + notes + question are sent to Claude (Roman's own account) for Q&A/summary.
- Audio never leaves the Mac and is deleted after finalize.
- Required permissions: Microphone, Screen & System Audio Recording, Accessibility is **not** required (hotkeys via `RegisterEventHotKey` / KeyboardShortcuts package).
- Recording people in person is Roman's responsibility; the menu bar dot makes recording state always visible.

## 10. Testing strategy (TDD)

- **Python unit (fast, no models):** framing parser, segmenter (synthetic tones + silence, VAD faked), hallucination filter, speaker labeler with fake embeddings, echo filter, session writer (crash/resume, fsync, md regeneration), control protocol round-trip, QA/summarize prompt building with a fake `claude` executable, finalize state machine.
- **Python integration (`-m models`, run locally on the Mac):** real mlx-whisper on fixtures (en/ru/uk + code-switch clip) asserting language and key words; real embeddings: Me-vs-Other accuracy on a two-voice fixture; end-to-end `WavAudioSource → transcript.jsonl` latency measurement.
- **Swift (XCTest):** frame encoder byte layout matches Python parser (shared golden file), overlay view-model (line trimming, fade state, end-of-session card state machine), EngineClient reconnect/buffering with a mock socket.
- **Contract test:** golden `audio_frames.bin` + `control_messages.ndjson` shared by both test suites.
- **Manual acceptance checklist:** in-person 2-person chat, Zoom call with headphones and with speakers, 3-hour soak (memory < 4 GB engine, no crash), kill -9 engine mid-session, unplug/plug AirPods, end-of-session save flow, project sync.

## 11. Dependencies (all free)

- Swift: SwiftUI, AVFoundation, ScreenCaptureKit, [KeyboardShortcuts](https://github.com/sindresorhus/KeyboardShortcuts) (MIT).
- Python 3.12 via `uv`: `mlx-whisper`, `silero-vad` (PyTorch; torch is already required by pyannote), `pyannote.audio` (requires free Hugging Face token + accepting model terms), `numpy`, `soundfile`, `pytest`.
- Claude Code CLI (`claude`) logged in with Roman's subscription.

## 12. Risks

1. **ScreenCaptureKit audio quirks** (permission prompts after updates) — mitigated by clear fix-it UI.
2. **Ru/Uk confusion** in short utterances — mitigated by allowed-language set and context prompt; accepted residual risk.
3. **Voiceprint threshold** varies by room/mic — `ontrack calibrate` can be added if τ needs tuning; τ is configurable.
4. **`claude -p` latency/limits** — single in-flight question, 60 s timeout, graceful failure.
5. **Memory with Zoom + browser on 16 GB** — budget: engine ≤ 4 GB peak; post-processing runs after recording stops.

## 13. Delivery milestones (detailed in the implementation plan)

1. M1 — Engine core: WAV in → live `transcript.jsonl/md` with Me/Other/Them (CLI only).
2. M2 — Swift capture + IPC; live transcript from real mic + system audio.
3. M3 — Overlay (live lines) + menu bar + hotkeys.
4. M4 — Q&A in overlay via `claude -p`.
5. M5 — End-of-session card, post-processing, summary, notes, Cowork project sync task.
6. M6 — Hardening: supervisor, reconnect, soak test, acceptance checklist.
