"""Core value types shared across the engine."""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, IntEnum

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 320  # 20 ms


class Channel(IntEnum):
    MIC = 0
    SYSTEM = 1


class Speaker(str, Enum):
    ME = "Me"
    OTHER = "Other"
    THEM = "Them"


@dataclass(frozen=True, eq=False)
class AudioFrame:
    channel: Channel
    t_ns: int
    samples: np.ndarray  # float32, mono, 16 kHz


@dataclass(frozen=True, eq=False)
class Utterance:
    channel: Channel
    t_start: float  # seconds since session origin
    t_end: float
    samples: np.ndarray

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start


@dataclass(frozen=True)
class Transcript:
    text: str
    lang: str
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float


@dataclass(frozen=True)
class Line:
    seq: int
    speaker: Speaker
    t_start: float
    t_end: float
    lang: str
    text: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "speaker": self.speaker.value,
                "t_start": self.t_start,
                "t_end": self.t_end,
                "lang": self.lang,
                "text": self.text,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, s: str) -> "Line":
        d = json.loads(s)
        return cls(
            seq=int(d["seq"]),
            speaker=Speaker(d["speaker"]),
            t_start=float(d["t_start"]),
            t_end=float(d["t_end"]),
            lang=str(d["lang"]),
            text=str(d["text"]),
        )
