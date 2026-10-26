"""Language-restricted transcription with per-channel context (spec §4.2)."""
from __future__ import annotations

from collections import deque
from typing import Protocol

import math

import numpy as np

from .config import Config
from .hallucination import normalize
from .types import Channel, Transcript, Utterance


class AsrBackend(Protocol):
    def detect_language(self, samples: np.ndarray) -> dict[str, float]: ...

    def run(self, samples: np.ndarray, language: str, initial_prompt: str | None) -> Transcript: ...


SHORT_KEEP_S = 1.5  # shorter utterances keep the channel's current language…
SHORT_KEEP_MIN_P = 0.15  # …unless language ID clearly says otherwise
CONFIDENT = 0.6  # language-ID probability above which we decode once, in that language
PRIOR_W = 0.3  # weight of language ID when comparing decodes (per-token logprob units)
STICKY = 0.2  # bonus for staying in the non-English language the channel was just speaking
EN_DOUBT = 0.1  # English decodes need a bit more evidence unless language ID is sure  # language-ID probability above which we decode once
# Whisper's language ID is confidently wrong toward Russian on Ukrainian speech, so a Russian pick is always
# checked against Ukrainian; a short native anchor prompt keeps Ukrainian spelling from drifting.
# Whisper copies the style of its prompt: a short, properly punctuated native sentence gives capitals,
# punctuation and the right orthography (Ukrainian і/ї/є instead of Russified spelling).
ANCHOR_PROMPTS = {
    "en": "Hello, welcome. Let's begin.",
    "nl": "Hallo, welkom. Laten we beginnen.",
    "uk": "Привіт, вітаю. Розмова українською мовою.",
}


class Transcriber:
    """Picks the language per utterance. Forcing Whisper into the wrong language makes it *translate*,
    so when language ID is unsure we decode in the top two languages and keep the more confident text."""

    def __init__(self, backend: AsrBackend, cfg: Config) -> None:
        self.backend = backend
        self.cfg = cfg
        self._context: dict[Channel, deque[tuple[str | None, str]]] = {}
        self._last_lang: dict[Channel, str] = {}
        self.languages: tuple[str, ...] | None = None  # per-session lock (menu → Language); None = auto

    @property
    def allowed(self) -> list[str]:
        return list(self.languages or self.cfg.allowed_langs)

    def _ctx(self, channel: Channel) -> deque[tuple[str | None, str]]:
        return self._context.setdefault(channel, deque(maxlen=8))

    def _prompt(self, channel: Channel, lang: str, duration: float = 99.0) -> str | None:
        if duration < 1.0:
            return None  # on sub-second audio a prompt mostly makes Whisper repeat the prompt
        # only same-language context: a Russian prompt pushes English speech into Russian
        same = [t for l, t in self._ctx(channel) if l is None or l == lang][-self.cfg.context_lines :]
        parts = [ANCHOR_PROMPTS[lang]] if lang in ANCHOR_PROMPTS else []
        if self.cfg.vocabulary:
            parts.append("Terms: " + ", ".join(self.cfg.vocabulary) + ".")
        parts += same
        return " ".join(parts) if parts else None

    def _run(self, samples, lang: str, prompt: str | None) -> Transcript:
        t = self.backend.run(samples, lang, prompt)
        norm = normalize(t.text)
        if prompt and norm:
            anchor = normalize(ANCHOR_PROMPTS.get(lang, ""))
            words = len(norm.split())  # a real single "Hello." must survive; "Hello, welcome." / "Let's begin." must not
            echoed = (anchor and words >= 2 and norm in anchor) or (words >= 4 and norm in normalize(prompt))
            if echoed:  # Whisper repeated (part of) its prompt on silence/noise
                return Transcript("", t.lang, t.avg_logprob, t.no_speech_prob, t.compression_ratio)
        return t

    def transcribe(self, u: Utterance) -> Transcript:
        allowed = self.allowed
        last = self._last_lang.get(u.channel)
        if last not in allowed:
            last = None
        if len(allowed) == 1:  # language locked for this session: no detection, no translation risk
            return self._decide(u, allowed, {allowed[0]: 1.0}, last)
        probs = dict(self.backend.detect_language(u.samples))
        if "uk" in allowed and "ru" not in allowed:  # Whisper's ID calls Ukrainian "Russian" a lot
            probs["uk"] = probs.get("uk", 0.0) + probs.pop("ru", 0.0)
        ranked = sorted(allowed, key=lambda l: (-probs.get(l, 0.0), allowed.index(l)))
        top = ranked[0]
        if probs.get(top, 0.0) >= CONFIDENT:
            candidates = [top]  # language ID is sure: decoding in another language would only *translate*
        elif last is not None and u.duration < SHORT_KEEP_S and probs.get(last, 0.0) >= SHORT_KEEP_MIN_P:
            candidates = [last]  # a second of audio: stay in the conversation's language unless ID clearly disagrees
        else:
            candidates = ranked[:2]
            if "ru" in candidates and "uk" in allowed and "uk" not in candidates:
                candidates.append("uk")
            if last is not None and last not in candidates:
                candidates.append(last)
        return self._decide(u, candidates, probs, last)

    def _decide(self, u: Utterance, candidates: list[str], probs: dict, last: str | None) -> Transcript:
        """Decode each candidate; score = Whisper's confidence + how likely language ID found that language.
        A Ukrainian phrase decoded "in English" is a fluent translation that can score well on its own,
        so the language-ID prior is what keeps it in Ukrainian."""
        results = [(l, self._run(u.samples, l, self._prompt(u.channel, l, u.duration))) for l in candidates]
        results = [(l, t) for l, t in results if t.text.strip()] or results

        def score(lt):
            l, t = lt
            sc = t.avg_logprob + PRIOR_W * math.log(max(probs.get(l, 0.0), 1e-3))
            if l == last and l != "en":
                sc += STICKY  # stay in Ukrainian/Dutch through unclear bits…
            if l == "en" and probs.get("en", 0.0) < CONFIDENT:
                sc -= EN_DOUBT  # …because the usual failure is Whisper *translating* into English, not the reverse
            return sc

        lang, best = max(results, key=score)
        self._last_lang[u.channel] = lang
        return best

    def preview(self, u: Utterance) -> Transcript:
        """Cheap single decode of an unfinished phrase, in the channel's current language."""
        allowed = self.allowed
        lang = self._last_lang.get(u.channel)
        if lang not in allowed:
            lang = allowed[0] if len(allowed) == 1 else None
        if lang is None:
            probs = self.backend.detect_language(u.samples)
            lang = sorted(allowed, key=lambda l: (-probs.get(l, 0.0), allowed.index(l)))[0]
        return self._run(u.samples, lang, self._prompt(u.channel, lang, u.duration))

    def remember(self, channel: Channel, text: str, lang: str | None = None) -> None:
        self._ctx(channel).append((lang, text))
