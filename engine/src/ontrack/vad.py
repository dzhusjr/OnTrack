"""Voice-activity-detection protocol."""
from __future__ import annotations

from typing import Protocol

import numpy as np


class Vad(Protocol):
    window: int  # samples per call (Silero: 512 @ 16 kHz)

    def speech_prob(self, window: np.ndarray) -> float: ...
