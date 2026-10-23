"""After-session speaker separation (Speaker A/B/C) and re-transcription (spec §4.2 postprocess)."""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import Config
from .hallucination import is_hallucination
from .recorder import load_channel
from .session import read_lines
from .speaker import Embedder, cosine
from .types import SAMPLE_RATE, Channel, Speaker, Utterance

log = logging.getLogger(__name__)
_MAX_CLUSTER_AUDIO_S = 30.0


class Diarizer(Protocol):
    def diarize(self, samples: np.ndarray) -> list[tuple[float, float, str]]: ...


@dataclass(frozen=True)
class FinalLine:
    seq: int
    speaker: str
    t_start: float
    t_end: float
    lang: str
    text: str


def _hms(t: float) -> str:
    s = int(t)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _span(audio: np.ndarray, t0: float, t1: float) -> np.ndarray:
    return audio[int(t0 * SAMPLE_RATE) : int(t1 * SAMPLE_RATE)]


def _cluster_names(segments, mic: np.ndarray, embedder: Embedder, voiceprint, cfg: Config) -> dict[str, str]:
    order: list[str] = []
    for _, _, label in sorted(segments):
        if label not in order:
            order.append(label)
    me: str | None = None
    if voiceprint is not None:
        best = -1.0
        for label in order:
            parts, total = [], 0.0
            for t0, t1, l in sorted(segments):
                if l == label and total < _MAX_CLUSTER_AUDIO_S:
                    parts.append(_span(mic, t0, t1))
                    total += t1 - t0
            audio = np.concatenate(parts) if parts else np.zeros(0, np.float32)
            if len(audio) < SAMPLE_RATE // 2:
                continue
            sim = cosine(embedder.embed(audio), voiceprint)
            if sim >= cfg.tau and sim > best:
                best, me = sim, label
    names, letter = {}, 0
    for label in order:
        if label == me:
            names[label] = Speaker.ME.value
        else:
            names[label] = f"Speaker {chr(ord('A') + letter)}"
            letter += 1
    return names


def _best_cluster(segments, t0: float, t1: float) -> str | None:
    best, best_ov = None, 0.0
    for s0, s1, label in segments:
        ov = min(t1, s1) - max(t0, s0)
        if ov > best_ov:
            best, best_ov = label, ov
    return best


def postprocess(
    session_dir: Path,
    audio_dir: Path,
    diarizer: Diarizer,
    embedder: Embedder,
    voiceprint: np.ndarray | None,
    transcriber,
    cfg: Config,
    progress: Callable[[str, float], None] | None = None,
) -> list[FinalLine]:
    session_dir = Path(session_dir)
    report = progress or (lambda stage, pct: None)
    lines = read_lines(session_dir)
    mic = load_channel(audio_dir, Channel.MIC)
    system = load_channel(audio_dir, Channel.SYSTEM)

    report("separating", 0.0)
    names: dict[str, str] = {}
    segments: list[tuple[float, float, str]] = []
    if len(mic):
        try:
            segments = list(diarizer.diarize(mic))
            names = _cluster_names(segments, mic, embedder, voiceprint, cfg)
        except Exception as e:  # spec §8: fall back to live labels
            log.warning("diarization failed, keeping live labels: %s", e)
            segments, names = [], {}

    final: list[FinalLine] = []
    for line in lines:
        speaker = line.speaker.value
        if line.speaker in (Speaker.ME, Speaker.OTHER) and segments:
            cluster = _best_cluster(segments, line.t_start, line.t_end)
            if cluster is not None:
                speaker = names.get(cluster, speaker)
        final.append(FinalLine(line.seq, speaker, line.t_start, line.t_end, line.lang, line.text))

    if transcriber is not None and cfg.retranscribe:
        n = max(1, len(final))
        for i, fl in enumerate(final):
            report("retranscribing", i / n)
            if fl.speaker == Speaker.NOTE.value:
                continue
            channel = Channel.SYSTEM if fl.speaker == Speaker.THEM.value else Channel.MIC
            audio = _span(system if channel == Channel.SYSTEM else mic, fl.t_start, fl.t_end)
            if len(audio) < SAMPLE_RATE // 4:
                continue
            t = transcriber.transcribe(Utterance(channel, fl.t_start, fl.t_end, audio))
            if not is_hallucination(t):
                final[i] = FinalLine(fl.seq, fl.speaker, fl.t_start, fl.t_end, t.lang, t.text.strip())
        report("retranscribing", 1.0)

    _write_final(session_dir, final)
    return final


def _write_final(session_dir: Path, final: list[FinalLine]) -> None:
    meta = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
    started = datetime.fromisoformat(meta["started_at"])
    md = [f"# {meta['name']}\n\n_Started {started:%Y-%m-%d %H:%M}_\n\n"]
    md += [f"`{_hms(l.t_start)}` **{l.speaker}:** {l.text}\n" for l in final]
    for name, content in (
        ("transcript.final.md", "".join(md)),
        ("transcript.final.jsonl", "".join(json.dumps(asdict(l), ensure_ascii=False) + "\n" for l in final)),
    ):
        tmp = session_dir / (name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, session_dir / name)


def read_final_lines(session_dir: Path) -> list[FinalLine]:
    p = Path(session_dir) / "transcript.final.jsonl"
    if not p.exists():
        return []
    return [FinalLine(**json.loads(l)) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
