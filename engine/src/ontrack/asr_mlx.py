"""mlx-whisper adapter for Apple Silicon (needs the `models` extra, macOS only)."""
from __future__ import annotations

import numpy as np

from .types import Transcript


class MlxWhisperBackend:
    def __init__(self, model_id: str) -> None:
        import mlx.core as mx
        import mlx_whisper
        from mlx_whisper.transcribe import ModelHolder

        self._mx = mx
        self._mlx_whisper = mlx_whisper
        self.model_id = model_id
        # Shared cache with mlx_whisper.transcribe(), so the model is loaded once.
        self._model = ModelHolder.get_model(model_id, mx.float16)

    def detect_language(self, samples: np.ndarray) -> dict[str, float]:
        from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim

        mel = log_mel_spectrogram(samples.astype(np.float32), n_mels=self._model.dims.n_mels, padding=N_SAMPLES)
        seg = pad_or_trim(mel, N_FRAMES, axis=-2).astype(self._mx.float16)
        _, probs = self._model.detect_language(seg)
        if isinstance(probs, list):
            probs = probs[0]
        return {k: float(v) for k, v in probs.items()}

    def run(self, samples: np.ndarray, language: str, initial_prompt: str | None) -> Transcript:
        r = self._mlx_whisper.transcribe(
            samples.astype(np.float32),
            path_or_hf_repo=self.model_id,
            language=language,
            initial_prompt=initial_prompt,
            condition_on_previous_text=False,
            word_timestamps=False,
            verbose=None,
        )
        segs = r.get("segments") or []
        if not segs:
            return Transcript("", language, 0.0, 1.0, 1.0)
        return Transcript(
            text=str(r.get("text", "")).strip(),
            lang=language,
            avg_logprob=float(np.mean([s["avg_logprob"] for s in segs])),
            no_speech_prob=float(max(s["no_speech_prob"] for s in segs)),
            compression_ratio=float(max(s["compression_ratio"] for s in segs)),
        )
