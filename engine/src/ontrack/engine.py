"""ontrack-engine: socket service between OnTrack.app and the transcription pipeline (spec §4.2, §5, §7, §8)."""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import logging
import json
import logging.handlers
import os
import queue
import re
import signal
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .audio_source import FrameDecoder
from .config import Config, apply_overrides, load_config
from .control import NdjsonDecoder, ProtocolError, encode, validate
from .finalize import FinalizeDeps, ProcessingDeferred, finalize, improve_saved, notes_dir_for, retry_summary
from .ai import AiRouter
from .llm import ClaudeRunner, LlmError
from .localai import LocalAi
from .pipeline import Backends, Lazy, Pipeline, real_backends
from .assist import (address_reason, build_interview_prompt, build_reply_prompt, build_suggest_prompt, name_regex,
                     transcript_tail)
from .connectors import needs_connectors
from .qa import QaService, fast_stream
from .style import SpeechStyle, conversation_language, style_block
from .summarize import LANG_NAMES
from .workdigest import WorkDigest
from .recorder import AudioRecorder, recorded_seconds
from .session import SessionWriter, paused_ranges, read_lines
from .speaker import enroll, load_voiceprint
from .transcriber import Transcriber
from .types import SAMPLE_RATE, AudioFrame, Channel, Line, Speaker

log = logging.getLogger("ontrack.engine")
ENROLL_SECONDS = 30
_SAFE_ID = re.compile(r"^[^/\\]+$")


PREVIEW_PAUSE_LAG_S = 1.5
PREVIEW_RESUME_LAG_S = 0.5
DEGRADED_MIN_S = 30.0


class LockedAsr:
    """Serialises all use of one ASR model (MLX is not thread-safe): warm-up, live pipeline, finalize."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._lock = threading.Lock()

    def detect_language(self, samples):
        with self._lock:
            return self._inner.detect_language(samples)

    def run(self, samples, language, initial_prompt):
        with self._lock:
            return self._inner.run(samples, language, initial_prompt)

    def warmup(self):
        with self._lock:
            warm = getattr(self._inner, "warmup", None)
            if warm:
                warm()


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


class EngineService:
    def __init__(self, cfg: Config, backends: Backends, runner, diarizer=None, fallback_asr=None, final_asr=None) -> None:
        self.cfg = cfg
        self.model_ready = not (isinstance(backends.asr, Lazy) or hasattr(backends.asr, "warmup"))
        self.backends = Backends(backends.vad_factory, LockedAsr(backends.asr), backends.embedder)
        self.diarizer = diarizer
        self.fallback_asr = LockedAsr(fallback_asr) if fallback_asr is not None else None
        # full large-v3 for the saved transcript (more accurate than the live turbo model)
        self.final_asr = LockedAsr(final_asr) if final_asr is not None else self.backends.asr
        # Claude by default; the optional local model is only ever loaded by an explicit request from the app
        self.ai = LocalAi(cfg, on_status=self._send_ai_status)
        self.runner = AiRouter(runner, cfg, self.ai)
        self.qa = QaService(self.runner, cfg)
        # per-session choices from the app (menu): project folder for Claude, language lock, reply helper
        self.opts: dict = {"project_dir": None, "languages": None, "assist": True, "interview": False,
                           "connectors": False}
        self._names = name_regex(cfg.my_names)
        self._suggest_lock = threading.Lock()
        self.style = SpeechStyle(cfg.app_support_dir / "my_speech.jsonl", cfg.sessions_dir)
        self.digest = WorkDigest()
        self._last_auto_suggest = -1e9
        self._answered_seq = -1  # replies cover lines up to here; the next Reply starts after it
        self.state = "idle"
        self.degraded = False
        self.writer: SessionWriter | None = None
        self.pipeline: Pipeline | None = None
        self.recorder: AudioRecorder | None = None
        self.last_session: Path | None = None
        self._voiceprint = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ctrl: asyncio.StreamWriter | None = None
        self._audio: asyncio.StreamWriter | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._work: queue.Queue = queue.Queue()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="ontrack")
        self._enroll: list[np.ndarray] = []
        self._last_t: dict[Channel, int] = {}
        self._first_t: int | None = None
        self._last_note_t = -1e9
        self._enqueued_t = 0
        self._processed_t = 0
        self._stopping: asyncio.Event | None = None
        self._socket_inodes: dict[Path, int] = {}
        self._finalizing: set[str] = set()
        self._defer_processing: dict[str, threading.Event] = {}
        self._resume_last_s: float | None = None
        self.dropped_frames = 0
        self._dropping_since: float | None = None
        self.live = True  # live transcription on; audio is recorded either way
        self.paused = False  # recording paused: audio is dropped, nothing saved
        self._rec_pause_start: float | None = None
        self._pause_start: float | None = None

    # ---- paths ------------------------------------------------------------------
    def _audio_dir(self, session_id: str) -> Path:
        return self.cfg.app_support_dir / "audio" / session_id

    # ---- lifecycle ----------------------------------------------------------------
    async def serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopping = asyncio.Event()
        d = self.cfg.app_support_dir
        d.mkdir(parents=True, exist_ok=True)
        ctrl_path, audio_path = d / "control.sock", d / "audio.sock"
        for p in (ctrl_path, audio_path):
            p.unlink(missing_ok=True)
        ctrl_srv = await asyncio.start_unix_server(self._on_control, path=str(ctrl_path))
        audio_srv = await asyncio.start_unix_server(self._on_audio, path=str(audio_path))
        self._socket_inodes = {p: os.stat(p).st_ino for p in (ctrl_path, audio_path)}
        worker = threading.Thread(target=self._work_loop, name="ontrack-pipeline", daemon=True)
        worker.start()
        if not self.model_ready:
            self._pool.submit(self._warmup)
        log.info("engine listening in %s", d)
        try:
            await self._stopping.wait()
        finally:
            for srv in (ctrl_srv, audio_srv):
                srv.close()
            for w in list(self._clients):
                w.close()
            for srv in (ctrl_srv, audio_srv):
                await srv.wait_closed()
            self._work.put(("quit", None))
            worker.join(timeout=5)
            for p, ino in self._socket_inodes.items():
                try:  # never delete a socket that a newer engine has created at the same path
                    if os.stat(p).st_ino == ino:
                        p.unlink()
                except FileNotFoundError:
                    pass
            self._pool.shutdown(wait=False, cancel_futures=True)
            log.info("engine stopped")

    async def shutdown(self) -> None:
        self.ai.stop()  # frees the local model, if one was loaded
        if self.state == "recording":
            await self._stop_session(notify=False)
        if self._stopping is not None:
            self._stopping.set()

    def _warmup(self) -> None:
        try:
            self.backends.asr.warmup()
        except Exception:
            log.exception("model warm-up failed")
        self._call(self._set_ready)

    def _set_ready(self) -> None:
        self.model_ready = True
        self._send_status()

    # ---- messaging ----------------------------------------------------------------------------
    def _call(self, fn, *args) -> None:
        """Run fn on the event loop thread (safe from worker threads)."""
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # loop closed during shutdown
            pass

    def _send_now(self, msg: dict) -> None:
        w = self._ctrl
        if w is not None and not w.is_closing():
            w.write(encode(msg))

    def send(self, msg: dict) -> None:
        if self._loop is not None and threading.current_thread() is threading.main_thread():
            self._send_now(msg)
        else:
            self._call(self._send_now, msg)

    def _send_status(self) -> None:
        self._send_now({
            "type": "status", "state": self.state, "model_ready": self.model_ready,
            "session_id": self.writer.id if self.writer and self.state in ("recording", "stopping") else None,
            "degraded": self.degraded, "live": self.live, "paused": self.paused,
        })

    def _send_ai_status(self) -> None:
        self.send(self.ai.status())

    def _set_state(self, state: str) -> None:
        self.state = state
        self._send_status()

    def _error(self, code: str, message: str, recoverable: bool = True) -> dict:
        return {"type": "error", "code": code, "message": message, "recoverable": recoverable}

    def _partial(self, channel: Channel, t_start: float, text: str) -> None:
        self.send({"type": "partial", "session_id": self.writer.id if self.writer else None,
                   "channel": int(channel), "t_start": round(t_start, 3), "text": text})

    def _line_msg(self, line: Line) -> dict:
        return {
            "type": "line", "session_id": self.writer.id if self.writer else None, "seq": line.seq,
            "speaker": line.speaker.value, "t_start": round(line.t_start, 3), "t_end": round(line.t_end, 3),
            "lang": line.lang, "text": line.text,
        }

    # ---- control socket -------------------------------------------------------------------------
    async def _on_control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._ctrl is not None:
            self._ctrl.close()
        self._ctrl = writer
        self._clients.add(writer)
        self._send_status()
        self._send_now(self.ai.status())
        dec = NdjsonDecoder()
        try:
            while data := await reader.read(65536):
                for msg in dec.feed(data):
                    try:
                        validate(msg)
                    except ProtocolError as e:
                        self._send_now(self._error("bad_message", str(e)))
                        continue
                    try:
                        await self._dispatch(msg)
                    except Exception as e:  # keep the engine alive whatever a message does
                        log.exception("handling %s failed", msg.get("type"))
                        self._send_now(self._error("internal", f"{type(e).__name__}: {e}"))
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.discard(writer)
            if self._ctrl is writer:
                self._ctrl = None
            writer.close()

    async def _dispatch(self, msg: dict) -> None:
        t = msg["type"]
        if t == "ping":
            self._send_now({"type": "pong", "id": msg.get("id"), "state": self.state})
        elif t == "start_session":
            self._start_session(msg["name"], msg.get("session_id"))
        elif t == "stop_session":
            if self.state != "recording":
                self._send_now(self._error("not_recording", "No session is recording."))
            else:
                await self._stop_session(notify=True)
        elif t == "ask":
            self._ask(msg["id"], msg["question"], msg.get("session_id"))
        elif t == "finalize":
            self._finalize(msg["session_id"], msg["action"], bool(msg.get("improve", True)))
        elif t == "defer_processing":
            sid = msg["session_id"]
            if sid in self._defer_processing:
                self._defer_processing[sid].set()
                self._send_now({"type": "finalize_progress", "session_id": sid, "stage": "deferring", "pct": 0})
        elif t == "continue_processing":
            folder = self._valid_session(msg["session_id"])
            if folder is None:
                self._send_now(self._error("no_session", "Recording not found."))
            else:
                marker = folder / ".pending_processing"
                options = json.loads(marker.read_text()) if marker.exists() else {}
                if options.get("mode") == "improve":
                    self._improve(msg["session_id"])
                else:
                    self._finalize(msg["session_id"], options.get("action", "save_all"), options.get("improve", True))
        elif t == "improve":
            self._improve(msg["session_id"])
        elif t == "retry_summary":
            self._retry_summary(msg["session_id"])
        elif t == "enroll_start":
            if self.state != "idle":
                self._send_now(self._error("busy", f"Cannot enroll while {self.state}."))
            else:
                self._enroll = []
                self._set_state("enrolling")
        elif t == "set_live":
            self._set_live(bool(msg["enabled"]))
        elif t == "set_paused":
            self._set_paused(bool(msg["paused"]))
        elif t == "set_session_options":
            self._set_options(msg)
        elif t == "suggest":
            self._suggest(str(msg["id"]), trigger=None, delay=0.0, auto=False)
        elif t == "set_ai":
            self.ai.configure(msg.get("mode"), msg.get("provider"),
                              {k: msg[k] for k in ("quick", "summary", "project") if k in msg})
        elif t == "local_ai":
            action = msg["action"]
            if action == "start":
                self.ai.start()
            elif action == "stop":
                self.ai.stop()
            else:
                self._pool.submit(self.ai.refresh)  # lists installed models (may ask Ollama), loads nothing
        elif t == "set_config":
            try:
                self.cfg = apply_overrides(self.cfg, msg["config"])
                self.qa.cfg = self.cfg
                self.runner.cfg = self.cfg
            except (ValueError, TypeError) as e:
                self._send_now(self._error("bad_config", str(e)))

    # ---- sessions -----------------------------------------------------------------------------------
    def _latest_lines(self, session_dir: Path, trigger_seq: int | None) -> tuple[str, int]:
        """The lines a reply is about: since the last answered one (or from the trigger), at most 4."""
        lines = [l for l in read_lines(session_dir) if l.speaker != Speaker.NOTE]
        start = trigger_seq if trigger_seq is not None else self._answered_seq + 1
        fresh = [l for l in lines if l.seq >= start][-4:] or lines[-3:]
        text = "\n".join(f"{l.speaker.value}: {l.text}" for l in fresh)
        return text, (fresh[-1].seq if fresh else self._answered_seq)

    def _start_session(self, name: str, session_id: str | None) -> None:
        if self.state == "recording":
            if session_id and self.writer and session_id == self.writer.id:
                self._send_status()
            else:
                self._send_now(self._error("already_recording", "A session is already recording."))
            return
        if self.state not in ("idle", "postprocessing"):
            self._send_now(self._error("busy", f"Cannot start while {self.state}."))
            return
        existing = self.cfg.sessions_dir / session_id if session_id else None
        if existing is not None and (existing / "meta.json").exists():
            self.writer = SessionWriter.resume(existing)
            lines = read_lines(existing)
            self._resume_last_s = max((l.t_end for l in lines), default=None)
            log.info("resumed session %s", self.writer.id)
        else:
            self.writer = SessionWriter.start(self.cfg.sessions_dir, name, datetime.now())
            self._resume_last_s = None
            log.info("started session %s", self.writer.id)
        self._answered_seq = -1
        self._voiceprint = load_voiceprint(self.cfg.app_support_dir / "voiceprint.npy")
        if self._voiceprint is None:
            self._send_now(self._error("no_voiceprint",
                                       "No voiceprint yet — menu → Enroll My Voice… so your lines show as Me."))
        self.pipeline = None
        self.recorder = None
        self._last_t.clear()
        self._first_t = None
        self._last_note_t = -1e9
        self._enqueued_t = self._processed_t = 0
        self.degraded = False
        self._degraded_since = 0.0
        self.dropped_frames = 0
        self._dropping_since = None
        self.live = True
        self._pause_start = None
        self.paused = False
        self._rec_pause_start = None
        self._set_state("recording")

    def _session_now(self) -> float | None:
        if not self._last_t or self.writer is None or self.writer.origin_ns is None:
            return None
        return (max(self._last_t.values()) - self.writer.origin_ns) / 1e9

    def _set_live(self, enabled: bool) -> None:
        if enabled == self.live:
            self._send_status()
            return
        self.live = enabled
        if self.state == "recording" and self.writer is not None:
            now = self._session_now()
            if not enabled:
                self._pause_start = now if now is not None else 0.0
                self._work.put(("flush", None))  # finish the phrase in progress
            elif self._pause_start is not None:
                end = now if now is not None else self._pause_start
                self.writer.add_paused(self._pause_start, end)
                self._work.put(("note", (self._pause_start,
                                         f"[live transcription paused {_mmss(end - self._pause_start)}]")))
                self._pause_start = None
        log.info("live transcription %s", "on" if enabled else "off")
        self._send_status()

    def _session_duration(self, writer: SessionWriter) -> float:
        """Best estimate of the session length. Frame timestamps alone are lost when the engine restarts
        mid-session (and nothing arrives before stop), so also use the audio on disk and the transcript."""
        candidates = [0.0]
        origin = writer.origin_ns
        if self._last_t and origin is not None:
            candidates.append((max(self._last_t.values()) - origin) / 1e9 + 0.02)
        try:
            candidates.append(recorded_seconds(self._audio_dir(writer.id)))
        except Exception:
            log.exception("could not measure recorded audio")
        try:
            candidates.append(max((l.t_end for l in read_lines(writer.dir)), default=0.0))
            candidates.extend(b for _, b in paused_ranges(writer.dir))
        except Exception:
            log.exception("could not read transcript for duration")
        return max(candidates)

    def _set_paused(self, paused: bool) -> None:
        """Pause = nothing is recorded or transcribed (audio is dropped); the timeline keeps running."""
        if self.state != "recording" or self.writer is None or paused == self.paused:
            self._send_status()
            return
        self.paused = paused
        now = self._session_now()
        if paused:
            self._rec_pause_start = now if now is not None else 0.0
            self._work.put(("flush", None))  # finish the phrase in progress
        else:
            self._end_recording_pause(now)
        log.info("recording %s", "paused" if paused else "resumed")
        self._send_status()

    def _end_recording_pause(self, now: float | None) -> None:
        if self._rec_pause_start is None:
            return
        start = self._rec_pause_start
        end = now if now is not None else start
        self._rec_pause_start = None
        self._last_note_t = end  # the jump in time is the pause, not a capture gap
        self._work.put(("note", (start, f"[recording paused {_mmss(end - start)}]")))

    async def _stop_session(self, notify: bool) -> None:
        if self.paused:
            self._end_recording_pause(self._session_now())
            self.paused = False
        if not self.live and self._pause_start is not None and self.writer is not None:
            end = self._session_now() or self._pause_start
            self.writer.add_paused(self._pause_start, end)
            self._work.put(("note", (self._pause_start, f"[live transcription paused {_mmss(end - self._pause_start)}]")))
            self._pause_start = None
        self._set_state("stopping")
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._work.put(("stop", fut))
        await asyncio.wrap_future(fut)
        writer = self.writer
        writer.close(datetime.now())
        if self.recorder:
            self.recorder.close()
        duration = self._session_duration(writer)
        self.last_session = writer.dir
        self.state = "idle"
        if notify:
            self._send_now({"type": "session_stopped", "session_id": writer.id,
                            "duration_s": round(duration, 2), "line_count": writer.next_seq})
            self._send_status()
        log.info("stopped session %s (%d lines)", writer.id, writer.next_seq)

    # ---- audio socket -----------------------------------------------------------------------------
    async def _on_audio(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._audio is not None:
            self._audio.close()
        self._audio = writer
        self._clients.add(writer)
        dec = FrameDecoder()
        try:
            while data := await reader.read(65536):
                for frame in dec.feed(data):
                    try:
                        self._on_frame(frame)
                    except Exception:
                        log.exception("frame handling failed")
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._clients.discard(writer)
            if self._audio is writer:
                self._audio = None
            writer.close()
            if dec.dropped:
                log.warning("audio decoder dropped %d malformed chunks", dec.dropped)

    def _on_frame(self, f: AudioFrame) -> None:
        if self.state == "enrolling":
            if f.channel == Channel.MIC:
                self._enroll.append(f.samples)
                if sum(len(x) for x in self._enroll) >= ENROLL_SECONDS * SAMPLE_RATE:
                    samples = np.concatenate(self._enroll)
                    self._enroll = []
                    self.state = "enroll_compute"
                    self._pool.submit(self._do_enroll, samples)
            return
        if self.state != "recording" or self.writer is None:
            return
        if self.writer.origin_ns is None:
            self.writer.set_origin(f.t_ns)
        origin = self.writer.origin_ns
        if self.pipeline is None:
            self.pipeline = Pipeline(self.cfg, self.backends, self.writer, self._voiceprint,
                                     on_line=self._on_line,
                                     fallback_asr=self.fallback_asr,
                                     on_partial=self._partial)
            self.pipeline.transcriber.languages = self.opts["languages"]
        if self.recorder is None:
            self.recorder = AudioRecorder(self._audio_dir(self.writer.id), origin)
        t_now = (f.t_ns - origin) / 1e9
        if self._resume_last_s is not None:
            if t_now - self._resume_last_s > self.cfg.gap_marker_s:
                self._last_note_t = self._resume_last_s
                self._work.put(("note", (self._resume_last_s, f"[gap {_mmss(t_now - self._resume_last_s)}]")))
            self._resume_last_s = None
        last = self._last_t.get(f.channel)
        if last is not None and f.t_ns - last > self.cfg.gap_marker_s * 1e9:
            gap_start = (last - origin) / 1e9
            if gap_start - self._last_note_t > 1.0:
                self._last_note_t = gap_start
                self._work.put(("note", (gap_start, f"[gap {_mmss((f.t_ns - last) / 1e9)}]")))
        self._last_t[f.channel] = f.t_ns
        if self.paused:
            return  # paused: keep the clock, drop the audio (the recorder pads the gap with silence)
        try:
            self.recorder.write(f)
        except Exception:
            log.exception("audio recording failed (disk full?) — transcript continues")
        if not self.live:
            return  # recording only: audio is saved above and transcribed when the session is saved
        if self._work.qsize() >= int(self.cfg.max_backlog_s * 50):
            # transcription is too far behind: skip live audio (it is still recorded for post-processing)
            self.dropped_frames += 1
            if self._dropping_since is None:
                self._dropping_since = t_now
                log.warning("backlog over %.0f s — dropping live audio", self.cfg.max_backlog_s)
            return
        if self._dropping_since is not None:
            self._work.put(("note", (self._dropping_since, f"[skipped {_mmss(t_now - self._dropping_since)} — transcription fell behind]")))
            self._dropping_since = None
        self._enqueued_t = max(self._enqueued_t, f.t_ns)
        self._work.put(("frame", f))

    # ---- pipeline worker thread ---------------------------------------------------------------------
    def _work_loop(self) -> None:
        n = 0
        while True:
            kind, item = self._work.get()
            try:
                if kind == "quit":
                    return
                if kind == "frame":
                    if self.pipeline is not None:
                        self.pipeline.process(item)
                    self._processed_t = item.t_ns
                    n += 1
                    if n % 50 == 0:
                        self._check_lag()
                elif kind == "note":
                    t, text = item
                    line = self.writer.append(Speaker.NOTE, t, t, "", text)
                    self.send(self._line_msg(line))
                elif kind == "flush":
                    if self.pipeline is not None:
                        self.pipeline.finish()
                elif kind == "stop":
                    try:
                        if self.pipeline is not None:
                            self.pipeline.finish()
                    finally:
                        item.set_result(True)
            except Exception as e:
                log.exception("pipeline error")
                self.send(self._error("pipeline", f"{type(e).__name__}: {e}"))

    def _check_lag(self) -> None:
        lag = (self._enqueued_t - self._processed_t) / 1e9
        if self.pipeline is None:
            return
        # 1st line of defence: drop the word-by-word preview while behind (it re-decodes open phrases)
        if not self.pipeline.previews_paused and lag > PREVIEW_PAUSE_LAG_S:
            self.pipeline.previews_paused = True
        elif self.pipeline.previews_paused and lag < PREVIEW_RESUME_LAG_S:
            self.pipeline.previews_paused = False
        # 2nd: smaller model. Stay in it a while so we don't flap between models every few seconds.
        now = time.monotonic()
        if not self.degraded and lag > self.cfg.degraded_lag_s and self.fallback_asr is not None:
            self.pipeline.set_degraded(True)
            self.degraded = True
            self._degraded_since = now
            log.warning("ASR lagging %.1f s — switching to fallback model", lag)
            self._call(self._send_status)
        elif self.degraded and lag < 2.0 and now - self._degraded_since >= DEGRADED_MIN_S:
            self.pipeline.set_degraded(False)
            self.degraded = False
            log.info("ASR caught up — back to the main model")
            self._call(self._send_status)

    # ---- background jobs ---------------------------------------------------------------------------
    # ---- session options & reply helper --------------------------------------------------------------
    def _set_options(self, msg: dict) -> None:
        if "project_dir" in msg:
            raw = msg.get("project_dir")
            p = Path(str(raw)).expanduser() if raw else None
            if p is not None and not p.is_dir():
                self._send_now(self._error("bad_project", f"Project folder not found: {p}"))
                p = None
            self.opts["project_dir"] = p
            self.digest.warm(p)  # git summary ready before the first Reply
        if "languages" in msg:
            langs = tuple(l for l in (msg.get("languages") or ()) if isinstance(l, str) and l)
            self.opts["languages"] = langs or None
            if self.pipeline is not None:
                self.pipeline.transcriber.languages = self.opts["languages"]
        if "names" in msg:
            words = tuple(str(w).strip() for w in (msg.get("names") or ()) if str(w).strip())
            self._names = name_regex(words or tuple(self.cfg.my_names))
        if msg.get("connectors") and not self.opts["connectors"]:
            from .llm import mcp_servers

            self._pool.submit(mcp_servers, self.cfg)  # discover Claude Code's connectors before the first answer
        for key in ("assist", "interview", "connectors"):
            if key in msg:
                self.opts[key] = bool(msg[key])
        log.info("session options: project=%s languages=%s assist=%s interview=%s connectors=%s",
                 self.opts["project_dir"], self.opts["languages"], self.opts["assist"], self.opts["interview"],
                 self.opts["connectors"])

    def _on_line(self, line: Line) -> None:
        """Pipeline thread: forward the line; notice when someone addresses you."""
        self.send(self._line_msg(line))
        if self.cfg.collect_my_speech:
            try:
                self.style.add(line)
            except OSError:
                log.exception("could not save your line for the reply style")
        reason = address_reason(line, self._names, self.opts["interview"])
        if reason is None:
            return
        self.send({"type": "addressed", "session_id": self.writer.id if self.writer else None, "seq": line.seq,
                   "reason": reason, "text": line.text})
        if not self.opts["assist"]:
            return
        now = time.monotonic()
        if reason == "question" and now - self._last_auto_suggest < self.cfg.suggest_min_gap_s:
            return
        if self._suggest_lock.locked():
            return  # one suggestion at a time; the newest context is picked up by the running one
        self._last_auto_suggest = now
        self._suggest(f"auto-{line.seq}", trigger=line.text, delay=self.cfg.suggest_delay_s, auto=True,
                      trigger_seq=line.seq)

    def _suggest(self, sid: str, trigger: str | None, delay: float, auto: bool,
                 trigger_seq: int | None = None) -> None:
        session_dir = self._session_dir_for_qa()
        if session_dir is None:
            self.send(self._error("no_session", "Start a session first."))
            self.send({"type": "suggestion_done", "id": sid})
            return
        project = self.opts["project_dir"]
        me = self.cfg.my_names[0] if self.cfg.my_names else "the user"

        def job() -> None:
            if not self._suggest_lock.acquire(blocking=not auto):
                self.send({"type": "suggestion_done", "id": sid})
                return
            try:
                if delay > 0:
                    time.sleep(delay)  # let the speaker finish the question
                said = trigger
                tail = transcript_tail(session_dir, self.cfg.suggest_context_lines)
                lang = conversation_language(read_lines(session_dir))
                style = style_block(me, LANG_NAMES.get(lang or "", None),
                                    self.style.samples(lang, self.cfg.style_samples))
                interview = bool(self.opts["interview"])
                # interview: general knowledge only — no project folder, no work summary, no connectors
                proj = None if interview else project
                work = self.digest.get(proj)
                latest, upto = self._latest_lines(session_dir, trigger_seq)  # after the delay: the full question
                # connectors only when the talk is about tickets, mail, calendar…: MCP start-up costs seconds
                connectors = (not interview and bool(self.opts["connectors"])
                              and needs_connectors(said or "", latest))
                name = proj.name if proj else None
                if interview:
                    prompt = build_interview_prompt(tail, me, style=style, latest=latest, trigger=said,
                                                    about=self.cfg.about_me)
                    said = said or "Reply"
                elif said is None:  # Reply button / ⌥⇧A: answer the latest question or add what's worth saying
                    said = "Reply"
                    prompt = build_reply_prompt(tail, me, name, work=work, style=style, connectors=connectors,
                                                latest=latest)
                else:
                    prompt = build_suggest_prompt(tail, said, me, name, work=work, style=style, connectors=connectors,
                                                  latest=latest)
                parts: list[str] = []
                for chunk in fast_stream(self.runner, self.cfg, prompt, self.cfg.qa_timeout_s, proj, connectors):
                    parts.append(chunk)
                    self.send({"type": "suggestion_delta", "id": sid, "text": chunk})
                reply = "".join(parts).strip()
                if reply.strip(" .—-–") == "":
                    reply = ""  # "nothing useful to say": nothing is shown or saved
                self._answered_seq = max(self._answered_seq, upto)  # the next Reply moves on to newer lines
                if reply:
                    with open(session_dir / "qa.md", "a", encoding="utf-8") as f:
                        f.write(f"## Suggested reply ({datetime.now():%H:%M}) to: {said.strip()}\n\n{reply}\n\n")
            except LlmError as e:
                self.send(self._error(e.code, e.message))
            except Exception as e:
                log.exception("suggestion failed")
                self.send(self._error("llm_failed", str(e)))
            finally:
                self._suggest_lock.release()
                self.send({"type": "suggestion_done", "id": sid})

        self._pool.submit(job)

    def _project_names(self) -> tuple[str, ...]:
        """The work folder's name and its repositories (e.g. search-api, …), offered to the summary for the session title."""
        p = self.opts["project_dir"]
        if p is None:
            return ()
        from .workdigest import find_repos

        try:
            return tuple(dict.fromkeys([p.name] + [r.name for r in find_repos(p)]))[:20]
        except OSError:
            return (p.name,)

    def _session_dir_for_qa(self) -> Path | None:
        if self.writer is not None and self.state in ("recording", "stopping"):
            return self.writer.dir
        return self.last_session

    def _notes_or_session_dir(self, session_id: str) -> Path | None:
        """A saved session's notes folder (preferred) or its raw session folder."""
        if not _SAFE_ID.match(session_id) or session_id in (".", ".."):
            return None
        for base in (self.cfg.notes_dir, self.cfg.sessions_dir):
            d = base / session_id
            if (d / "transcript.md").exists():
                return d
        return None

    def _ask(self, qid: str, question: str, session_id: str | None = None) -> None:
        session_dir = self._notes_or_session_dir(session_id) if session_id else self._session_dir_for_qa()
        if session_dir is None:
            self._send_now(self._error("no_session", "Start a session first."))
            self._send_now({"type": "answer_done", "id": qid})
            return

        def job() -> None:
            try:
                for chunk in self.qa.ask(session_dir, question, project_dir=self.opts["project_dir"],
                                         connectors=bool(self.opts["connectors"]) and needs_connectors(question)):
                    self.send({"type": "answer_delta", "id": qid, "text": chunk})
            except LlmError as e:
                self.send(self._error(e.code, e.message))
            except Exception as e:
                log.exception("Q&A failed")
                self.send(self._error("llm_failed", str(e)))
            finally:
                self.send({"type": "answer_done", "id": qid})

        self._pool.submit(job)

    def _valid_session(self, session_id: str) -> Path | None:
        if not _SAFE_ID.match(session_id) or session_id in (".", ".."):
            return None
        d = self.cfg.sessions_dir / session_id
        return d if (d / "meta.json").exists() else None

    def _finalize_deps(self) -> FinalizeDeps:
        transcriber = Transcriber(self.final_asr, self.cfg) if self.cfg.retranscribe else None
        return FinalizeDeps(self.diarizer, self.backends.embedder,
                            load_voiceprint(self.cfg.app_support_dir / "voiceprint.npy"),
                            transcriber, self.runner, self.cfg, vad_factory=self.backends.vad_factory,
                            projects=self._project_names())

    def _improve(self, session_id: str) -> None:
        session_dir = self._valid_session(session_id)
        audio = self._audio_dir(session_id) if session_dir is not None else None
        if session_dir is None or audio is None or not audio.exists():
            self._send_now(self._error("no_audio", "This session can't be improved: its audio is no longer kept."))
            return
        if session_id in self._finalizing:
            self._send_now(self._error("busy", "That session is already being processed."))
            return
        self._finalizing.add(session_id)
        cancel = threading.Event()
        self._defer_processing[session_id] = cancel
        if self.state == "idle":
            self._set_state("postprocessing")
        notes, deps = notes_dir_for(self.cfg.notes_dir, session_id), self._finalize_deps()
        deps.cancelled = cancel.is_set
        (session_dir / ".pending_processing").write_text(json.dumps({"mode": "improve"}))

        def job() -> None:
            try:
                out = improve_saved(session_dir, audio, notes, deps,
                                    lambda stage, pct: self.send({"type": "finalize_progress", "session_id": session_id,
                                                                  "stage": stage, "pct": round(pct, 3)}),
                                    on_warning=lambda text: self.send(self._error("diarization_unavailable", text)))
                self.send({"type": "finalize_done", "session_id": session_id, "notes_path": str(out)})
            except ProcessingDeferred:
                self.send({"type": "processing_deferred", "session_id": session_id})
            except Exception as e:
                log.exception("improve failed")
                self.send(self._error("finalize_failed", str(e)))
            finally:
                self._defer_processing.pop(session_id, None)
                self._finalizing.discard(session_id)
                self._call(self._finalize_finished)

        self._pool.submit(job)

    def _finalize(self, session_id: str, action: str, improve: bool = True) -> None:
        session_dir = self._valid_session(session_id)
        if session_dir is None:
            self._send_now(self._error("no_session", f"Unknown session {session_id}"))
            return
        recording_this = self.writer is not None and self.writer.id == session_id and self.state in ("recording", "stopping")
        if recording_this or session_id in self._finalizing:
            self._send_now(self._error("busy", "That session is still recording or already being saved."))
            return
        self._finalizing.add(session_id)
        cancel = threading.Event()
        self._defer_processing[session_id] = cancel
        if self.state == "idle":
            self._set_state("postprocessing")
        deps = self._finalize_deps()
        deps.cancelled = cancel.is_set
        if action != "discard":
            (session_dir / ".pending_processing").write_text(json.dumps({"mode": "finalize", "action": action, "improve": improve}))

        def job() -> None:
            try:
                notes = finalize(
                    session_dir, self._audio_dir(session_id), action, deps,
                    lambda stage, pct: self.send({"type": "finalize_progress", "session_id": session_id,
                                                  "stage": stage, "pct": round(pct, 3)}),
                    on_summary_error=lambda e: self.send(self._error(
                        "summary_failed", f"Summary failed ({e.code}): {e.message} — use Retry Summary in the menu.")),
                    on_warning=lambda text: self.send(self._error("diarization_unavailable", text)),
                    improve=improve,
                )
                if action == "discard" and self.last_session == session_dir:
                    self.last_session = None
                self.send({"type": "finalize_done", "session_id": session_id, "notes_path": str(notes) if notes else ""})
            except ProcessingDeferred:
                self.send({"type": "processing_deferred", "session_id": session_id})
            except Exception as e:
                log.exception("finalize failed")
                self.send(self._error("finalize_failed", str(e)))
            finally:
                self._defer_processing.pop(session_id, None)
                self._finalizing.discard(session_id)
                self._call(self._finalize_finished)

        self._pool.submit(job)

    def _finalize_finished(self) -> None:
        if self.state == "postprocessing" and not self._finalizing:
            self._set_state("idle")

    def _retry_summary(self, session_id: str) -> None:
        session_dir = self._valid_session(session_id)
        if session_dir is None:
            self._send_now(self._error("no_session", f"Unknown session {session_id}"))
            return
        notes = notes_dir_for(self.cfg.notes_dir, session_id)

        def job() -> None:
            try:
                retry_summary(notes, session_dir, self.runner, self.cfg)
                self.send({"type": "finalize_done", "session_id": session_id, "notes_path": str(notes)})
            except LlmError as e:
                self.send(self._error(e.code, e.message))
            except Exception as e:
                self.send(self._error("finalize_failed", str(e)))

        self._pool.submit(job)

    def _do_enroll(self, samples: np.ndarray) -> None:
        try:
            score = enroll(self.backends.embedder, samples, self.cfg.app_support_dir / "voiceprint.npy")
            self.send({"type": "enroll_done", "score": round(score, 3)})
        except Exception as e:
            log.exception("enrollment failed")
            self.send(self._error("enroll_failed", str(e)))
        finally:
            self._call(self._set_state, "idle")


# ---- entry point ----------------------------------------------------------------------------------------
def _setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_dir / "engine.log", maxBytes=10_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if os.isatty(2):  # under OnTrack.app stderr goes to a file; avoid logging everything twice
        root.addHandler(logging.StreamHandler())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ontrack-engine")
    ap.add_argument("--config")
    args = ap.parse_args(argv)
    default = load_config(None).app_support_dir / "config.toml"
    cfg = load_config(Path(args.config) if args.config else default)
    _setup_logging(cfg.app_support_dir / "logs")
    from .models_setup import apply_offline_mode

    if apply_offline_mode(cfg):
        log.info("models verified — offline mode (no Hugging Face calls)")
    else:
        log.warning("models not verified offline — run scripts/dev.sh setup (Hugging Face is contacted on load)")

    def fallback():
        from .asr_mlx import MlxWhisperBackend

        return MlxWhisperBackend(cfg.fallback_whisper_model)

    def diarizer():
        from .diarize_pyannote import PyannoteDiarizer

        return PyannoteDiarizer()

    def final():
        from .asr_mlx import MlxWhisperBackend

        return MlxWhisperBackend(cfg.final_whisper_model)

    svc = EngineService(cfg, real_backends(cfg), ClaudeRunner(cfg), diarizer=Lazy(diarizer),
                        fallback_asr=Lazy(fallback), final_asr=Lazy(final))

    async def run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(svc.shutdown()))
        await svc.serve()

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
