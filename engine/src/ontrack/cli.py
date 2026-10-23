"""`ontrack` command line: transcribe WAV files, enroll your voice."""
from __future__ import annotations

import argparse
import heapq
import sys
from datetime import datetime
from pathlib import Path

from .audio_source import WavAudioSource, load_wav_16k
from .config import Config, load_config
from .pipeline import Backends, Pipeline, merge_key, real_backends
from .session import SessionWriter
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


def main(argv: list[str] | None = None, backends: Backends | None = None) -> int:
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
    args = p.parse_args(argv)
    if args.cmd == "transcribe":
        return _transcribe(args, backends)
    return _enroll(args, backends)


if __name__ == "__main__":
    sys.exit(main())
