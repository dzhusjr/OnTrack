import numpy as np
import pytest
from fakes import EnergyVad, frames, silence, tone

from ontrack.config import load_config
from ontrack.segmenter import Segmenter
from ontrack.types import Channel


def run(samples):
    seg = Segmenter(Channel.MIC, EnergyVad(), load_config(None), origin_ns=0)
    out = []
    for f in frames(samples, Channel.MIC):
        out += seg.push(f)
    return out, seg


def test_single_utterance_after_600ms_silence():
    out, _ = run(np.concatenate([silence(1.0), tone(2.0), silence(1.0)]))
    assert len(out) == 1
    assert out[0].t_start == pytest.approx(0.8, abs=0.04)  # 200 ms pre-pad
    assert out[0].t_end == pytest.approx(3.0, abs=0.1)
    assert out[0].channel == Channel.MIC


def test_not_emitted_before_end_silence_then_flush():
    out, seg = run(np.concatenate([tone(1.0), silence(0.4)]))
    assert out == []
    assert len(seg.flush()) == 1


def test_silence_only_yields_nothing():
    out, seg = run(silence(5.0))
    assert out == [] and seg.flush() == []


def test_long_speech_split_at_quiet_point():
    x = tone(20.0)
    i = int(13.5 * 16000)
    x[i : i + 1600] = 0.02  # quieter but still speech
    out, seg = run(x)
    out += seg.flush()
    assert out[0].t_end == pytest.approx(13.55, abs=0.1)
    assert all(u.duration <= 15.0 + 0.2 for u in out)


def test_long_speech_split_preserves_all_samples():  # Review Focus 5
    out, seg = run(tone(40.0))
    out += seg.flush()
    assert len(out) >= 3
    assert sum(len(u.samples) for u in out) == pytest.approx(40.0 * 16000, abs=512)
    for a, b in zip(out, out[1:]):
        assert b.t_start == pytest.approx(a.t_end, abs=1e-3)  # contiguous, no overlap


def test_origin_offsets_timestamps():
    seg = Segmenter(Channel.SYSTEM, EnergyVad(), load_config(None), origin_ns=1_000_000_000)
    out = []
    for f in frames(np.concatenate([tone(1.0), silence(1.0)]), Channel.SYSTEM, start_ns=3_000_000_000):
        out += seg.push(f)
    assert len(out) == 1 and out[0].t_start == pytest.approx(2.0, abs=0.04)
    assert out[0].channel == Channel.SYSTEM
