"""pyannote speaker-diarization adapter (needs the `models` extra and a free HF token)."""
from __future__ import annotations

import os

import numpy as np

MODEL_ID = "pyannote/speaker-diarization-3.1"


class PyannoteDiarizer:
    def __init__(self, token: str | None = None) -> None:
        import torch
        from pyannote.audio import Pipeline

        token = token if token is not None else os.environ.get("HF_TOKEN")
        try:
            pipe = Pipeline.from_pretrained(MODEL_ID, token=token)  # pyannote >= 4
        except TypeError:
            pipe = Pipeline.from_pretrained(MODEL_ID, use_auth_token=token)  # pyannote 3.x
        if pipe is None:
            raise RuntimeError(f"Could not load {MODEL_ID}; accept its terms on Hugging Face and log in.")
        if torch.backends.mps.is_available():
            pipe.to(torch.device("mps"))
        self._torch = torch
        self._pipe = pipe

    def diarize(self, samples: np.ndarray) -> list[tuple[float, float, str]]:
        wav = self._torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32))[None]
        out = self._pipe({"waveform": wav, "sample_rate": 16000})
        ann = getattr(out, "speaker_diarization", out)  # 4.x DiarizeOutput vs 3.x Annotation
        return [(float(t.start), float(t.end), str(label)) for t, _, label in ann.itertracks(yield_label=True)]
