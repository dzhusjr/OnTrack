"""Live speaker labelling: channel + voiceprint verification (spec §4.2, D4)."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

from .config import Config
from .types import SAMPLE_RATE, Channel, Speaker, Utterance


class Embedder(Protocol):
    def embed(self, samples: np.ndarray) -> np.ndarray: ...


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class SpeakerLabeler:
    def __init__(self, embedder: Embedder, voiceprint: np.ndarray | None, cfg: Config) -> None:
        self.embedder = embedder
        self.voiceprint = voiceprint
        self.cfg = cfg
        self._prev: tuple[Speaker, float] | None = None  # (label, t_end) of last mic utterance

    @property
    def has_voiceprint(self) -> bool:
        return self.voiceprint is not None

    def label(self, u: Utterance) -> Speaker:
        if u.channel == Channel.SYSTEM:
            return Speaker.THEM
        if self.voiceprint is None:
            return Speaker.ME
        if (
            self._prev is not None
            and u.duration < self.cfg.short_utt_s
            and u.t_start - self._prev[1] <= self.cfg.neighbor_gap_s
        ):
            label = self._prev[0]
        else:
            sim = cosine(self.embedder.embed(u.samples), self.voiceprint)
            label = Speaker.ME if sim >= self.cfg.tau else Speaker.OTHER
        self._prev = (label, u.t_end)
        return label


def load_voiceprint(path: Path) -> np.ndarray | None:
    path = Path(path)
    return np.load(path) if path.exists() else None


def enroll(embedder: Embedder, samples: np.ndarray, out_path: Path, window_s: float = 3.0) -> float:
    win = int(window_s * SAMPLE_RATE)
    n = len(samples) // win
    if n < 3:
        raise ValueError(f"Need at least {3 * window_s:.0f} s of speech to enroll (got {len(samples) / SAMPLE_RATE:.1f} s)")
    embs = []
    for i in range(n):
        e = np.asarray(embedder.embed(samples[i * win : (i + 1) * win]), dtype=np.float64)
        embs.append(e / (np.linalg.norm(e) or 1.0))
    vp = np.mean(embs, axis=0)
    vp = vp / (np.linalg.norm(vp) or 1.0)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(out_path, vp)
    return float(np.mean([cosine(e, vp) for e in embs]))
