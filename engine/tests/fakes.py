"""Test doubles and signal helpers (no ML dependencies)."""
from __future__ import annotations

import numpy as np

from ontrack.types import FRAME_SAMPLES, SAMPLE_RATE, AudioFrame, Channel


class EnergyVad:
    window = 512

    def speech_prob(self, window: np.ndarray) -> float:
        return 1.0 if float(np.sqrt(np.mean(window**2))) > 0.01 else 0.0


def tone(seconds: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(round(seconds * SAMPLE_RATE))) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(round(seconds * SAMPLE_RATE)), np.float32)


def frames(samples: np.ndarray, channel: Channel, start_ns: int = 0) -> list[AudioFrame]:
    pad = (-len(samples)) % FRAME_SAMPLES
    x = np.concatenate([samples, np.zeros(pad, np.float32)]) if pad else samples
    step = FRAME_SAMPLES * 1_000_000_000 // SAMPLE_RATE
    return [
        AudioFrame(channel, start_ns + (i // FRAME_SAMPLES) * step, x[i : i + FRAME_SAMPLES].copy())
        for i in range(0, len(x), FRAME_SAMPLES)
    ]


# ---- Task 5: ASR ---------------------------------------------------------
from ontrack.types import Transcript, Utterance  # noqa: E402


def tr(text: str, lang: str = "en") -> Transcript:
    return Transcript(text, lang, -0.2, 0.0, 1.0)


def utt(channel: Channel = Channel.MIC, t_start: float = 0.0, t_end: float = 2.0, amp: float = 0.3) -> Utterance:
    n = int(round((t_end - t_start) * SAMPLE_RATE))
    return Utterance(channel, t_start, t_end, np.full(n, amp, np.float32))


class FakeAsr:
    """Scripted backend: queue of (lang_probs, Transcript); records (language, initial_prompt)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[str, str | None]] = []
        self._current = None

    def detect_language(self, samples):
        self._current = self.script.pop(0)
        return self._current[0]

    def run(self, samples, language, initial_prompt):
        self.calls.append((language, initial_prompt))
        return self._current[1]
