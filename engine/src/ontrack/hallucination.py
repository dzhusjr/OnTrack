"""Rejects typical Whisper hallucinations on silence/noise (spec §4.2)."""
from __future__ import annotations

import re

from .types import Transcript

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


BLOCKLIST: frozenset[str] = frozenset(
    normalize(s)
    for s in (
        "thanks for watching",
        "thank you for watching",
        "please subscribe",
        "subscribe to the channel",
        "продолжение следует",
        "спасибо за просмотр",
        "субтитры сделал dimatorzok",
        "субтитры создавал dimatorzok",
        "редактор субтитров а семкин корректор а егорова",
        "дякую за перегляд",
        "ondertiteling door de amara org gemeenschap",
        "ondertiteld door de amara org gemeenschap",
        "bedankt voor het kijken",
    )
)


# Phrases Whisper invents on short noise/breaths; kept only when decoded with high confidence.
SHORT_SUSPECTS: frozenset[str] = frozenset(
    normalize(s)
    for s in ("thank you", "thanks", "thank you very much", "see you soon", "see you", "bye", "bye bye", "you",
              "дякую", "дякую вам", "до побачення", "dank je", "dank je wel", "bedankt")
)


def is_hallucination(t: Transcript, duration: float | None = None) -> bool:
    norm = normalize(t.text)
    if not norm or norm in BLOCKLIST:
        return True
    if t.no_speech_prob > 0.6 and t.avg_logprob < -1.0:
        return True
    if t.compression_ratio > 2.4:
        return True
    if duration is not None:
        if duration < 1.0 and (t.no_speech_prob > 0.3 or t.avg_logprob < -0.7):
            return True  # sub-second blip that Whisper isn't sure about
        confident = t.avg_logprob >= -0.25 and t.no_speech_prob <= 0.05
        if duration < 1.5 and norm in SHORT_SUSPECTS and not confident:
            return True
    return False
