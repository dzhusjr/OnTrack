# OnTrack M2–M6 — Full System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. the user asked (2026-10-23) to implement M2–M6 in one run and test at the end; plan review gate waived by him explicitly.

**Goal:** Turn the M1 engine into the full OnTrack system from the spec: engine service with sockets, Q&A and summaries via `claude -p`, post-processing, end-of-session save flow, and the SwiftUI menu-bar app with capture and the overlay.

**Architecture:** Python `ontrack-engine` (asyncio) serves `audio.sock` and `control.sock` in the app-support dir; a single worker thread runs the M1 `Pipeline`. The Swift app (XcodeGen project) = Foundation-only `OnTrackCore` package (unit-tested on Linux and Mac) + macOS app target (capture, sockets, supervisor, menu bar, hotkeys, overlay).

**Tech Stack:** Python 3.12/asyncio, pytest; Swift 5.9+ (tools 5.9), SwiftUI/AppKit, AVFoundation, ScreenCaptureKit, Carbon hotkeys, XcodeGen.

**Spec:** `docs/superpowers/specs/2026-10-23-ontrack-design.md`

## Global Constraints

- All M1 constraints hold. Wire formats exactly per spec §5; message `type` names verbatim.
- Sockets: `<app_support_dir>/audio.sock`, `<app_support_dir>/control.sock`; engine is the server, accepts reconnects.
- `claude` invoked headless: `claude -p --output-format stream-json --verbose --include-partial-messages` + `--model <llm_model>`, prompt on stdin, cwd = an empty temp dir, timeout 60 s (Q&A) / 300 s (summary). One Q&A in flight; others queued.
- Overlay: last **3** lines collapsed (config 1–5), width 560 pt, corner radius 14, hotkeys ⌥Space (overlay) and ⌥⇧S (start/stop).
- Audio temp files under `<app_support_dir>/audio/<session_id>/{mic,system}-NNNN.flac` (60 s chunks); deleted after finalize.
- Notes under `notes_dir/<session_id>/`; `.pending_sync` marker for the Cowork sync task.
- GUI apps do not inherit shell env: engine finds `claude` via config `claude_path` or a PATH extended with `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `~/.claude/local`; HF token via `HF_TOKEN` or the huggingface_hub stored login (`hf auth login`).
- Swift Core must compile with Swift 5.10 on Linux (Foundation only, no Combine/AppKit).

## Review Focus

1. Engine killed mid-session → app restarts it, same session resumes, timestamps continue (Task 10 `test_resume_same_session_id_continues_seq_and_time`).
2. `claude` missing/not logged in/times out → overlay error, transcript still saved (Task 5 `test_missing_binary_error_code`, `test_timeout_kills_process`; Task 9 `test_save_all_summary_failure_still_saves_transcript`).
3. App disconnects and reconnects mid-session → no duplicate lines in overlay (`seq` de-dup, Core `OverlayReducerTests.testDuplicateSeqIgnored`).
4. Long 3 h session → bounded memory: echo list, segmenter buffers, hold queue, overlay lines all bounded (Task 11 soak).
5. Discard requires confirmation and really deletes audio + session folder; save never deletes the session folder (Task 9 tests).

---

### Task 1: Config, NOTE speaker, segmenter introspection
Files: `config.py`, `types.py`, `segmenter.py`, tests. Adds Config fields `notes_dir`, `llm_model="sonnet"`, `claude_path=""`, `qa_timeout_s=60`, `summary_timeout_s=300`, `fallback_whisper_model="mlx-community/whisper-small-mlx"`, `degraded_lag_s=20.0`, `mic_hold_max_s=15.0`, `gap_marker_s=10.0`, `overlay_lines=3`, `retranscribe=True`; `Speaker.NOTE="Note"`; `Segmenter.position_s` (seconds processed) and `Segmenter.open_start_s` (None when idle).
Tests: defaults; `test_position_and_open_start_reported`.

### Task 2: Pipeline mic hold + degraded ASR
`Pipeline` holds MIC utterances until the SYSTEM segmenter's `position_s ≥ u.t_end + echo_window_s` and its open utterance (if any) starts after `u.t_end + echo_window_s`, or the hold exceeds `mic_hold_max_s`, or no system channel exists; `finish()` releases all in time order. `Pipeline(..., fallback_asr: AsrBackend | None)`; `set_degraded(bool)` switches the transcriber backend; property `degraded`.
Tests: `test_mic_fragment_during_open_them_utterance_is_dropped_as_echo`, `test_mic_only_session_not_delayed`, `test_hold_released_after_max`, `test_degraded_switches_backend`.

### Task 3: Control protocol (`control.py`)
`encode(msg: dict) -> bytes` (compact JSON + `\n`), `NdjsonDecoder.feed(bytes) -> list[dict]` (skips invalid lines, counts `invalid`), `validate(msg) -> dict` raising `ProtocolError` on unknown `type`/missing required fields. Required fields per spec §5. Golden file `tests/fixtures/golden/control_messages.ndjson` (one of each type) shared with Swift.
Tests: roundtrip, partial chunks, invalid line skipped, unknown type rejected, golden file validates.

### Task 4: Audio recorder (`recorder.py`)
`AudioRecorder(audio_dir: Path, chunk_s=60)`: `write(frame)`, `close()`; FLAC per channel, chunk files `mic-0000.flac`…; `load_channel(audio_dir, channel) -> np.ndarray` concatenates; `delete_audio(audio_dir)`.
Tests: roundtrip samples equal (FLAC lossless within 1e-4 after 16-bit), chunk rollover, delete.

### Task 5: Claude runner (`llm.py`)
`find_claude(cfg) -> Path | None`; `class LlmError(Exception)` with `code in {"llm_missing","llm_auth","llm_timeout","llm_rate_limit","llm_failed"}`; `ClaudeRunner(cfg).stream(prompt, timeout_s) -> Iterator[str]` yields text deltas from `stream_event`/`content_block_delta` lines, falls back to `assistant` message text when no deltas, raises on `result.is_error` (maps "rate limit"/"429" → rate_limit, "login"/"auth"/"401" → auth). `complete(prompt, timeout_s) -> str`.
Tests use a fake `claude` Python script on PATH: `test_streams_partial_deltas`, `test_assistant_fallback_without_deltas`, `test_missing_binary_error_code`, `test_timeout_kills_process`, `test_rate_limit_mapped`, `test_prompt_sent_on_stdin`.

### Task 6: Q&A service (`qa.py`)
`build_qa_prompt(transcript_md, notes_md, question) -> str`; `QaService(runner, cfg)`: `ask(session_dir, question) -> Iterator[str]` (serialised by a lock), appends `## Q (HH:MM)\n…\n\n**A:** …\n\n` to `qa.md`; on `LlmError` appends `_(unanswered: code)_` and re-raises.
Tests: prompt contains transcript, notes, question and language instruction; qa.md written; unanswered marker.

### Task 7: Summarizer (`summarize.py`)
`build_summary_prompt(transcript, notes, qa) -> str` (sections: TL;DR, Key points, Decisions, Action items (owner), Open questions, Glossary; answer in the transcript's dominant language); `summarize(runner, session_dir, cfg) -> str`.
Tests: dominant language computed from lines; prompt sections; output written.

### Task 8: Post-processing (`postprocess.py`, `diarize_pyannote.py`)
`class Diarizer(Protocol): diarize(samples) -> list[tuple[float,float,str]]`. `postprocess(session_dir, audio_dir, diarizer, embedder, voiceprint, transcriber|None, cfg) -> list[Line]`: maps each MIC line to the diarization cluster with max overlap; cluster with voiceprint similarity ≥ tau (best) → `Me`, others → `Speaker A/B/C…` by first appearance; THEM/NOTE unchanged; if `transcriber` given and `cfg.retranscribe`, re-run ASR on the line's audio span; writes `transcript.final.md` (+ `.jsonl`). On diarizer exception → live labels, log warning. `PyannoteDiarizer` adapter (`pyannote/speaker-diarization-3.1`), models-marked test.
Tests: relabel A/B, Me detection, overlap mapping, fallback on error, retranscribe uses audio span.

### Task 9: Finalize (`finalize.py`)
`finalize(session_dir, audio_dir, action, deps, progress: Callable[[str,float],None]) -> Path | None`. `save_all`: postprocess → summarize → write `notes/<id>/{summary.md, transcript.md, qa.md}` → delete audio → `.pending_sync`; summary failure → `summary_pending` file in notes dir, transcript still saved. `transcript_only`: no summary. `discard`: delete audio + session dir. `retry_summary(notes_dir, session_dir, runner, cfg)`.
Tests: each action's files; summary failure path; discard deletes; progress stages in order `separating`, `retranscribing`, `summarizing`, `saving`.

### Task 10: Engine service (`engine.py`)
`EngineService(cfg, backends, runner)` asyncio: control server (NDJSON, multiple sequential clients, broadcasts to the current client), audio server (frames → recorder + worker queue). Handles `ping/start_session{name, session_id?}/stop_session/ask/finalize/enroll_start/set_config`. States idle|recording|stopping|postprocessing. Gap > `gap_marker_s` → NOTE line `[gap 00:12]`. Lag > `degraded_lag_s` → `set_degraded(True)` + `status{degraded:true}` until lag < 2 s. Enroll mode: collects 30 s of MIC frames → `enroll_done{score}`. Logging to `<app_support>/logs/engine.log` (rotating 10 MB × 5). Entrypoint `ontrack-engine`.
Tests (fake backends, real sockets in short tmp dir): start→frames→`line` messages; stop→`session_stopped`; finalize→`finalize_progress`…`finalize_done`; ask→`answer_delta`+`answer_done` (fake runner); resume same session id continues seq/time; enroll flow; unknown message → `error`.

### Task 11: Soak (marker `soak`)
Simulated 3 h, two channels, fake backends, frames fed directly to `Pipeline`: tracemalloc peak growth < 50 MB after the first 10 minutes; echo/hold/segmenter sizes bounded.

### Task 12: Swift `OnTrackCore` (Linux-testable)
`FrameEncoder.encode(channel:tNs:samples:) -> Data` (golden test against `engine/tests/fixtures/golden/audio_frames.bin`); `ControlMessage` enum Codable (golden test decodes every line of `control_messages.ndjson`); `NdjsonDecoder`; `FrameChunker` (accumulate 16 kHz samples → 320-sample frames, timestamps by sample count anchored to host time, re-anchor if drift > 200 ms); `OverlayState` + `reduce(state, event)` (keeps last N lines, de-dups `seq`, Q&A streaming, end-of-session card, progress, errors); `Backoff` (100 ms → 2 s); `AudioRingBuffer` (≤ 10 s of frames while disconnected).

### Task 13: macOS app target
`app/project.yml` (XcodeGen; LSUIElement, NSMicrophoneUsageDescription, ad-hoc signing, macOS 14+), `AppDelegate`, `EngineSupervisor` (launch `engine/.venv/bin/ontrack-engine`, ping 2 s, restart ≤ 5 s, resume session), `EngineClient` (POSIX unix sockets, reconnect, ring buffer), `CaptureService` (AVAudioEngine VPIO mic with ducking minimised; SCStream audio; AVAudioConverter → 16 kHz mono; FrameChunker), `HotkeyService` (Carbon), `MenuBarController`, `OverlayPanel` + SwiftUI views bound to `OverlayStore` (ObservableObject wrapping the Core reducer), `Permissions` (fix-it deep links).

### Task 14: Project sync + docs
Cowork scheduled task (daily, requires the Mac) syncing `notes/*/.pending_sync` into the OnTrack Claude Project; `README.md` (setup, build, run), `docs/acceptance.md` (spec §10 manual checklist), `scripts/dev.sh` (sync engine, generate Xcode project, build).
