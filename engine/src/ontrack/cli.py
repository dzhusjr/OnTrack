"""`ontrack` command line: transcribe WAV files, enroll your voice."""
from __future__ import annotations

import argparse
import heapq
import sys
from datetime import datetime
from pathlib import Path

from .audio_source import WavAudioSource, load_wav_16k
from .config import Config, load_config
from .finalize import FinalizeDeps, finalize
from .pipeline import Backends, Lazy, Pipeline, merge_key, real_backends
from .session import SessionWriter, read_lines
from .transcriber import Transcriber
from .speaker import enroll, load_voiceprint
from .types import Channel, Line

HINT = "Tip: run 'ontrack enroll' so OnTrack can tell you apart from others."


def _hms(t: float) -> str:
    s = int(t)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _print_line(line: Line) -> None:
    print(f"[{_hms(line.t_start)}] {line.speaker.value}: {line.text}", flush=True)


def _config(args) -> Config:
    path = Path(args.config) if args.config else load_config(None).app_support_dir / "config.toml"
    return load_config(path)


def _transcribe(args, backends: Backends | None) -> int:
    cfg = _config(args)
    sessions_dir = Path(args.sessions_dir) if args.sessions_dir else cfg.sessions_dir
    backends = backends or real_backends(cfg)
    vp = load_voiceprint(cfg.app_support_dir / "voiceprint.npy")
    if vp is None:
        print(HINT)
    writer = SessionWriter.start(sessions_dir, args.name, datetime.now())
    pipe = Pipeline(cfg, backends, writer, vp, on_line=_print_line)
    sources = [WavAudioSource(Path(args.mic), Channel.MIC).iter_frames()]
    if args.system:
        sources.append(WavAudioSource(Path(args.system), Channel.SYSTEM).iter_frames())
    for frame in heapq.merge(*sources, key=merge_key):
        pipe.process(frame)
    pipe.finish()
    writer.close(datetime.now())
    print(f"Session saved: {writer.dir}")
    return 0


def _enroll(args, backends: Backends | None) -> int:
    cfg = _config(args)
    backends = backends or real_backends(cfg)
    try:
        score = enroll(backends.embedder, load_wav_16k(Path(args.wav)), cfg.app_support_dir / "voiceprint.npy")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Voiceprint saved (self-similarity {score:.2f})")
    return 0


def _sessions(args) -> int:
    """Sessions whose temporary audio still exists, i.e. not saved or discarded yet."""
    cfg = _config(args)
    audio_root = cfg.app_support_dir / "audio"
    pending = sorted(p.name for p in audio_root.iterdir() if p.is_dir()) if audio_root.exists() else []
    if not pending:
        print("No unsaved sessions.")
    for sid in pending:
        lines = len(read_lines(cfg.sessions_dir / sid)) if (cfg.sessions_dir / sid).exists() else 0
        print(f"{sid}  ({lines} lines)")
    return 0


def _finalize_cmd(args, backends: Backends | None, diarizer, runner) -> int:
    cfg = _config(args)
    session_dir = cfg.sessions_dir / args.session_id
    if "/" in args.session_id or not (session_dir / "meta.json").exists():
        print(f"Unknown session {args.session_id} — see 'ontrack sessions'", file=sys.stderr)
        return 2
    injected = backends is not None  # tests pass fakes; real runs use full large-v3 for the saved transcript
    backends = backends or real_backends(cfg)
    if diarizer is None:
        from .diarize_pyannote import PyannoteDiarizer

        diarizer = Lazy(PyannoteDiarizer)
    if runner is None:
        from .llm import ClaudeRunner

        runner = ClaudeRunner(cfg)
    final_asr = backends.asr
    if not injected and args.final_model:
        from .asr_mlx import MlxWhisperBackend

        final_asr = MlxWhisperBackend(cfg.final_whisper_model)
    deps = FinalizeDeps(diarizer, backends.embedder, load_voiceprint(cfg.app_support_dir / "voiceprint.npy"),
                        Transcriber(final_asr, cfg) if cfg.retranscribe else None, runner, cfg,
                        vad_factory=backends.vad_factory)
    notes = finalize(session_dir, cfg.app_support_dir / "audio" / args.session_id, args.action, deps,
                     lambda stage, pct: print(f"  {stage} {pct:.0%}", flush=True),
                     on_summary_error=lambda e: print(f"! summary failed ({e.code}): {e.message}", file=sys.stderr),
                     on_warning=lambda text: print(f"! {text}", file=sys.stderr))
    print(f"Saved to {notes}" if notes else "Discarded.")
    return 0


def main(argv: list[str] | None = None, backends: Backends | None = None, diarizer=None, runner=None) -> int:
    p = argparse.ArgumentParser(prog="ontrack")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transcribe", help="transcribe WAV recordings into a session folder")
    t.add_argument("mic")
    t.add_argument("--system")
    t.add_argument("--name", required=True)
    t.add_argument("--config")
    t.add_argument("--sessions-dir")
    e = sub.add_parser("enroll", help="save your voiceprint from a recording of you speaking")
    e.add_argument("--wav", required=True)
    e.add_argument("--config")
    ls = sub.add_parser("sessions", help="list sessions that were never saved (e.g. app quit or crashed)")
    ls.add_argument("--config")
    fz = sub.add_parser("finalize", help="save (or discard) an unsaved session")
    fz.add_argument("session_id")
    fz.add_argument("--action", default="save_all", choices=["save_all", "transcript_only", "discard"])
    fz.add_argument("--config")
    fz.add_argument("--no-final-model", dest="final_model", action="store_false",
                    help="re-transcribe with the live model instead of full large-v3")
    args = p.parse_args(argv)
    if args.cmd == "sessions":
        return _sessions(args)
    if args.cmd == "finalize":
        return _finalize_cmd(args, backends, diarizer, runner)
    if args.cmd == "transcribe":
        return _transcribe(args, backends)
    return _enroll(args, backends)


if __name__ == "__main__":
    sys.exit(main())
