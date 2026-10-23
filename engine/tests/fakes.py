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
