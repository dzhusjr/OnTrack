"""pyannote speaker-embedding adapter (needs the `models` extra and a free HF token)."""
from __future__ import annotations

import os

import numpy as np


def _stored_token() -> str | None:
    """Token saved by `hf auth login`. Passed explicitly: pyannote treats token=None as anonymous for gated repos."""
    try:
        from huggingface_hub import get_token

        return get_token()
    except Exception:
        return None

MODEL_ID = "pyannote/wespeaker-voxceleb-resnet34-LM"


class PyannoteEmbedder:
    def __init__(self, token: str | None = None) -> None:
        import torch
        from pyannote.audio import Inference, Model

        token = token or os.environ.get("HF_TOKEN") or _stored_token()
        try:
            model = Model.from_pretrained(MODEL_ID, token=token)  # pyannote >= 4
        except TypeError:
            model = Model.from_pretrained(MODEL_ID, use_auth_token=token)  # pyannote 3.x
        if model is None:
            raise RuntimeError(f"Could not load {MODEL_ID}; set HF_TOKEN and accept the model terms on Hugging Face.")
        self._torch = torch
        self._inference = Inference(model, window="whole")
        if torch.backends.mps.is_available():
            self._inference.to(torch.device("mps"))

    def embed(self, samples: np.ndarray) -> np.ndarray:
        wav = self._torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32))[None]
        e = np.asarray(self._inference({"waveform": wav, "sample_rate": 16000}), dtype=np.float64).reshape(-1)
        return e / (np.linalg.norm(e) or 1.0)
