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


@pytest.mark.parametrize("speech_s", [14.6, 14.7, 14.8, 14.9])
def test_split_inside_pause_never_emits_empty_utterance(speech_s):  # review #1
    out, seg = run(np.concatenate([tone(speech_s), silence(2.0)]))
    out += seg.flush()
    assert out and all(len(u.samples) > 0 and u.t_end > u.t_start for u in out)


def test_flush_after_split_in_pause_never_empty():  # review #1
    out, seg = run(np.concatenate([tone(14.7), silence(0.4)]))
    out += seg.flush()
    assert all(len(u.samples) > 0 for u in out)


def test_gap_in_frames_keeps_timestamps_and_closes_utterance():  # review #5
    seg = Segmenter(Channel.MIC, EnergyVad(), load_config(None), origin_ns=0)
    out = []
    for f in frames(tone(1.0), Channel.MIC):
        out += seg.push(f)
    for f in frames(np.concatenate([tone(1.0), silence(1.0)]), Channel.MIC, start_ns=10_000_000_000):
        out += seg.push(f)
    assert len(out) == 2
    assert out[0].t_end <= 1.05
    assert out[1].t_start == pytest.approx(10.0, abs=0.05)


def test_stale_frame_is_dropped():  # review #5
    seg = Segmenter(Channel.MIC, EnergyVad(), load_config(None), origin_ns=0)
    fs = frames(np.concatenate([tone(1.0), silence(1.0)]), Channel.MIC)
    out = []
    for f in fs[:30] + fs[0:15] + fs[30:]:  # 300 ms of stale/duplicate frames replayed
        out += seg.push(f)
    assert len(out) == 1 and len(out[0].samples) <= 1.0 * 16000 + 512 + 1600  # ≤100 ms jitter tolerance
