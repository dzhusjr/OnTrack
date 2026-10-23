"""3-hour simulated session (fake models): memory and internal buffers stay bounded.
Run: uv run pytest -m soak -o addopts="" -s   (SOAK_HOURS env overrides the default 3)."""
import os
import resource
import sys
from dataclasses import replace

import numpy as np
import pytest
from fakes import NOW, EnergyVad, FakeEmbedder, silence, tone

from ontrack.config import load_config
from ontrack.pipeline import Backends, Pipeline
from ontrack.session import SessionWriter, read_lines
from ontrack.types import FRAME_SAMPLES, AudioFrame, Channel, Transcript

pytestmark = pytest.mark.soak


class EndlessAsr:
    def __init__(self):
        self.n = 0

    def detect_language(self, samples):
        return {"en": 1.0}

    def run(self, samples, language, initial_prompt):
        self.n += 1
        words = " ".join(f"w{self.n}x{k}" for k in range(6))  # unique tokens: no accidental echo matches
        return Transcript(words, "en", -0.2, 0.0, 1.0)


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1e6 if sys.platform == "darwin" else r / 1e3


def test_three_hour_session_is_bounded(tmp_path):
    hours = float(os.environ.get("SOAK_HOURS", "3"))
    cfg = replace(load_config(None), sessions_dir=tmp_path)
    w = SessionWriter.start(tmp_path, "soak", NOW)
    p = Pipeline(cfg, Backends(EnergyVad, EndlessAsr(), FakeEmbedder()), w, np.array([1.0, 0, 0]))
    mic_period = np.concatenate([tone(5.0, 0.3), silence(2.0), tone(20.0, 0.5), silence(1.0)])  # 28 s, incl. >15 s talk
    sys_period = np.concatenate([tone(8.0, 0.3), silence(3.0)])  # 11 s
    step = FRAME_SAMPLES * 1_000_000_000 // 16000
    total = int(hours * 3600 * 16000 / FRAME_SAMPLES)
    baseline = None
    max_held = max_echo = 0
    for i in range(total):
        s = i * FRAME_SAMPLES
        mi, si = s % len(mic_period), s % len(sys_period)
        mic = mic_period[mi : mi + FRAME_SAMPLES]
        sysf = sys_period[si : si + FRAME_SAMPLES]
        if len(mic) < FRAME_SAMPLES:
            mic = np.concatenate([mic, mic_period[: FRAME_SAMPLES - len(mic)]])
        if len(sysf) < FRAME_SAMPLES:
            sysf = np.concatenate([sysf, sys_period[: FRAME_SAMPLES - len(sysf)]])
        p.process(AudioFrame(Channel.SYSTEM, i * step, sysf))
        p.process(AudioFrame(Channel.MIC, i * step, mic))
        if i == int(600 * 16000 / FRAME_SAMPLES):  # after 10 min
            baseline = rss_mb()
        max_held = max(max_held, len(p._held))
        max_echo = max(max_echo, len(p.echo._recent))
    p.finish()
    lines = read_lines(w.dir)
    growth = rss_mb() - (baseline or rss_mb())
    print(f"\nsoak {hours} h: {len(lines)} lines, rss growth {growth:.1f} MB, max held {max_held}, max echo {max_echo}")
    assert len(lines) >= hours * 3600 * (3 / 28 + 1 / 11) * 0.95  # 3 mic utts per period (20 s split) + 1 system
    assert growth < 50
    assert max_held <= 5 and max_echo <= 10
    for seg in p._segmenters.values():
        assert seg._buf_len <= 15 * 16000 + 512
