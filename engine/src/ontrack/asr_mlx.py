"""mlx-whisper adapter for Apple Silicon (needs the `models` extra, macOS only)."""
from __future__ import annotations

import threading

import numpy as np

from .types import Transcript

# mlx_whisper.transcribe() keeps ONE model in a class-level cache and reloads (from disk + Hub check)
# whenever a different model is asked for. Live, fast-mode and final models alternate, so each call
# pins its own already-loaded model into that cache under this lock instead of triggering a reload.
_HOLDER_LOCK = threading.Lock()


class MlxWhisperBackend:
    def __init__(self, model_id: str) -> None:
        import mlx.core as mx
        import mlx_whisper
        from mlx_whisper.transcribe import ModelHolder

        self._mx = mx
        self._mlx_whisper = mlx_whisper
        self.model_id = model_id
        # Loaded once; run() pins it into mlx_whisper's single-slot cache for each call.
        with _HOLDER_LOCK:
            self._model = ModelHolder.get_model(model_id, mx.float16)

    def warmup(self) -> None:
        """Compile Metal kernels before the first real utterance."""
        self.run(np.zeros(16000, np.float32), "en", None)

    def detect_language(self, samples: np.ndarray) -> dict[str, float]:
        from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim

        with _HOLDER_LOCK:
            return self._detect(samples, N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim)

    def _detect(self, samples, N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim) -> dict[str, float]:
        mel = log_mel_spectrogram(samples.astype(np.float32), n_mels=self._model.dims.n_mels, padding=N_SAMPLES)
        seg = pad_or_trim(mel, N_FRAMES, axis=-2).astype(self._mx.float16)
        _, probs = self._model.detect_language(seg)
        if isinstance(probs, list):
            probs = probs[0]
        return {k: float(v) for k, v in probs.items()}

    def run(self, samples: np.ndarray, language: str, initial_prompt: str | None) -> Transcript:
        from mlx_whisper.transcribe import ModelHolder

        with _HOLDER_LOCK:
            ModelHolder.model, ModelHolder.model_path = self._model, self.model_id
            r = self._transcribe(samples, language, initial_prompt)
        return self._to_transcript(r, language)

    def _transcribe(self, samples: np.ndarray, language: str, initial_prompt: str | None) -> dict:
        return self._mlx_whisper.transcribe(
            samples.astype(np.float32),
            path_or_hf_repo=self.model_id,
            language=language,
            initial_prompt=initial_prompt,
            condition_on_previous_text=False,
            temperature=0.0,  # no fallback re-decodes: bounded latency; hallucination filter rejects bad decodes
            word_timestamps=False,
            verbose=None,
        )

    @staticmethod
    def _to_transcript(r: dict, language: str) -> Transcript:
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
