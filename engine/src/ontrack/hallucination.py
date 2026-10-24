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


def is_hallucination(t: Transcript) -> bool:
    norm = normalize(t.text)
    if not norm or norm in BLOCKLIST:
        return True
    if t.no_speech_prob > 0.6 and t.avg_logprob < -1.0:
        return True
    return t.compression_ratio > 2.4
