"""Language-restricted transcription with per-channel context (spec §4.2)."""
from __future__ import annotations

from collections import deque
from typing import Protocol

import numpy as np

from .config import Config
from .types import Channel, Transcript, Utterance


class AsrBackend(Protocol):
    def detect_language(self, samples: np.ndarray) -> dict[str, float]: ...

    def run(self, samples: np.ndarray, language: str, initial_prompt: str | None) -> Transcript: ...


class Transcriber:
    def __init__(self, backend: AsrBackend, cfg: Config) -> None:
        self.backend = backend
        self.cfg = cfg
        self._context: dict[Channel, deque[str]] = {}

    def _ctx(self, channel: Channel) -> deque[str]:
        return self._context.setdefault(channel, deque(maxlen=self.cfg.context_lines))

    def transcribe(self, u: Utterance) -> Transcript:
        probs = self.backend.detect_language(u.samples)
        allowed = self.cfg.allowed_langs
        lang = max(allowed, key=lambda l: (probs.get(l, 0.0), -allowed.index(l)))
        ctx = self._ctx(u.channel)
        prompt = " ".join(ctx) if ctx else None
        return self.backend.run(u.samples, lang, prompt)

    def remember(self, channel: Channel, text: str) -> None:
        self._ctx(channel).append(text)
