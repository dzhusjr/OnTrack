"""Two mlx backends alternating must not reload weights (mlx_whisper caches only one model)."""
import sys
import types

import numpy as np


def install_fakes(monkeypatch):
    loads = []

    class ModelHolder:
        model = None
        model_path = None

        @classmethod
        def get_model(cls, path, dtype):
            if cls.model is None or path != cls.model_path:
                loads.append(path)
                cls.model, cls.model_path = object(), path
            return cls.model

    def transcribe(audio, *, path_or_hf_repo, **kw):
        m = ModelHolder.get_model(path_or_hf_repo, None)
        return {"text": f"{path_or_hf_repo}:{id(m)}", "segments": [
            {"avg_logprob": -0.1, "no_speech_prob": 0.0, "compression_ratio": 1.0}]}

    tmod = types.ModuleType("mlx_whisper.transcribe")
    tmod.ModelHolder = ModelHolder
    pkg = types.ModuleType("mlx_whisper")
    pkg.transcribe = transcribe
    pkg.__path__ = []
    mx = types.ModuleType("mlx.core")
    mx.float16 = "f16"
    mlx = types.ModuleType("mlx")
    mlx.core = mx
    for name, mod in {"mlx": mlx, "mlx.core": mx, "mlx_whisper": pkg, "mlx_whisper.transcribe": tmod}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return loads


def test_alternating_models_load_each_once(monkeypatch):
    loads = install_fakes(monkeypatch)
    from ontrack.asr_mlx import MlxWhisperBackend

    turbo, small = MlxWhisperBackend("turbo"), MlxWhisperBackend("small")
    x = np.zeros(1600, np.float32)
    outs = [b.run(x, "en", None).text for b in (turbo, small, turbo, small, turbo)]
    assert loads == ["turbo", "small"]
    assert outs[0] == outs[2] == outs[4] == f"turbo:{id(turbo._model)}"
    assert outs[1] == outs[3] == f"small:{id(small._model)}"
