"""Silero VAD adapter (needs the `models` extra)."""
from __future__ import annotations

import numpy as np


class SileroVad:
    window = 512

    def __init__(self) -> None:
        import torch
        from silero_vad import load_silero_vad

        torch.set_num_threads(1)
        self._torch = torch
        self._model = load_silero_vad()

    def speech_prob(self, window: np.ndarray) -> float:
        x = self._torch.from_numpy(np.ascontiguousarray(window, dtype=np.float32))
        with self._torch.no_grad():
            return float(self._model(x, 16000).item())
