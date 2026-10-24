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


CONFIDENT = 0.8  # language-ID probability above which we decode once
# Whisper's language ID is confidently wrong toward Russian on Ukrainian speech, so a Russian pick is always
# checked against Ukrainian; a short native anchor prompt keeps Ukrainian spelling from drifting.
ANCHOR_PROMPTS = {"uk": "Розмова українською мовою.", "nl": "Een gesprek in het Nederlands."}


class Transcriber:
    """Picks the language per utterance. Forcing Whisper into the wrong language makes it *translate*,
    so when language ID is unsure we decode in the top two languages and keep the more confident text."""

    def __init__(self, backend: AsrBackend, cfg: Config) -> None:
        self.backend = backend
        self.cfg = cfg
        self._context: dict[Channel, deque[tuple[str | None, str]]] = {}
        self._last_lang: dict[Channel, str] = {}

    def _ctx(self, channel: Channel) -> deque[tuple[str | None, str]]:
        return self._context.setdefault(channel, deque(maxlen=8))

    def _prompt(self, channel: Channel, lang: str) -> str | None:
        # only same-language context: a Russian prompt pushes English speech into Russian
        same = [t for l, t in self._ctx(channel) if l is None or l == lang][-self.cfg.context_lines :]
        return " ".join(same) if same else ANCHOR_PROMPTS.get(lang)

    def transcribe(self, u: Utterance) -> Transcript:
        probs = self.backend.detect_language(u.samples)
        allowed = list(self.cfg.allowed_langs)
        ranked = sorted(allowed, key=lambda l: (-probs.get(l, 0.0), allowed.index(l)))
        candidates = ranked[:1] if probs.get(ranked[0], 0.0) >= CONFIDENT or len(ranked) < 2 else ranked[:2]
        if "ru" in candidates and "uk" in allowed and "uk" not in candidates:
            candidates.append("uk")
        results = [(l, self.backend.run(u.samples, l, self._prompt(u.channel, l))) for l in candidates]
        results = [(l, t) for l, t in results if t.text.strip()] or results
        best_lp = max(t.avg_logprob for _, t in results)
        close = [(l, t) for l, t in results if t.avg_logprob >= best_lp - self.cfg.lang_tie_margin]
        lang, best = min(close, key=lambda lt: allowed.index(lt[0]))
        self._last_lang[u.channel] = lang
        return best

    def preview(self, u: Utterance) -> Transcript:
        """Cheap single decode of an unfinished phrase, in the channel's current language."""
        lang = self._last_lang.get(u.channel)
        if lang is None:
            probs = self.backend.detect_language(u.samples)
            allowed = list(self.cfg.allowed_langs)
            lang = sorted(allowed, key=lambda l: (-probs.get(l, 0.0), allowed.index(l)))[0]
        return self.backend.run(u.samples, lang, self._prompt(u.channel, lang))

    def remember(self, channel: Channel, text: str, lang: str | None = None) -> None:
        self._ctx(channel).append((lang, text))
