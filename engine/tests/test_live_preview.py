from dataclasses import replace

import numpy as np
import pytest
from fakes import NOW, EnergyVad, FakeEmbedder, frames, silence, tone

from ontrack.config import load_config
from ontrack.pipeline import Backends, Pipeline
from ontrack.session import SessionWriter, read_lines
from ontrack.types import Channel, Transcript


class CountingAsr:
    def __init__(self):
        self.n = 0
        self.runs = 0

    def detect_language(self, samples):
        return {"en": 1.0}

    def run(self, samples, language, initial_prompt):
        self.runs += 1
        self.n += 1
        return Transcript(f"words {self.n}", "en", -0.2, 0.0, 1.0)


def make(tmp_path, **cfg_over):
    cfg = replace(load_config(None), sessions_dir=tmp_path, **cfg_over)
    w = SessionWriter.start(tmp_path, "T", NOW)
    partials, lines = [], []
    asr = CountingAsr()
    p = Pipeline(cfg, Backends(EnergyVad, asr, FakeEmbedder()), w, None, on_line=lines.append,
                 on_partial=lambda ch, t0, text: partials.append((ch, t0, text)))
    return p, w, partials, lines, asr


def test_partials_stream_during_speech_then_clear_and_final(tmp_path):
    p, w, partials, lines, _ = make(tmp_path)
    for f in frames(np.concatenate([tone(3.0), silence(1.0)]), Channel.MIC):
        p.process(f)
    shown = [x for x in partials if x[2]]
    assert len(shown) >= 3  # roughly every 0.7 s of speech
    assert all(ch == Channel.MIC and t0 == pytest.approx(0.0, abs=0.05) for ch, t0, _ in shown)
    ch, t0, text = partials[-1]
    assert text == "" and t0 == pytest.approx(lines[0].t_start)  # cleared for exactly the finished utterance
    assert [l.text for l in read_lines(w.dir)] == [lines[0].text]  # previews never written to files
    assert not any(l.text == s[2] for l in read_lines(w.dir) for s in shown)


def test_preview_interval_is_respected(tmp_path):
    p, w, partials, lines, _ = make(tmp_path, preview_interval_s=1.0)
    for f in frames(np.concatenate([tone(5.0), silence(1.0)]), Channel.MIC):
        p.process(f)
    assert 3 <= len([x for x in partials if x[2]]) <= 6


def test_no_partials_when_disabled(tmp_path):
    p, w, partials, lines, asr = make(tmp_path, live_preview=False)
    for f in frames(np.concatenate([tone(3.0), silence(1.0)]), Channel.MIC):
        p.process(f)
    assert partials == [] and asr.runs == 1


def test_no_partials_while_degraded(tmp_path):
    p, w, partials, lines, asr = make(tmp_path)
    p._fallback_asr = CountingAsr()
    p.set_degraded(True)
    for f in frames(np.concatenate([tone(3.0), silence(1.0)]), Channel.MIC):
        p.process(f)
    assert [x for x in partials if x[2]] == []
