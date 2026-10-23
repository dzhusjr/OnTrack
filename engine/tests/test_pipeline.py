from dataclasses import replace

import numpy as np
from fakes import ME_VP, NOW, EnergyVad, FakeAsr, FakeEmbedder, frames, silence, tone, tr

from ontrack.config import load_config
from ontrack.pipeline import Backends, Pipeline
from ontrack.session import SessionWriter, read_lines
from ontrack.types import Channel, Speaker


def make(tmp_path, asr_script, vp=ME_VP, on_line=None):
    cfg = replace(load_config(None), sessions_dir=tmp_path)
    w = SessionWriter.start(tmp_path, "T", NOW)
    return Pipeline(cfg, Backends(EnergyVad, FakeAsr(asr_script), FakeEmbedder()), w, vp, on_line), w


def by_time(fs):
    # at equal timestamps the system channel is processed first (see pipeline.merge_key)
    return sorted(fs, key=lambda f: (f.t_ns, -int(f.channel)))


def test_end_to_end_labels_and_order(tmp_path):
    p, w = make(tmp_path, [({"en": 1}, tr("hi I'm Roman")), ({"ru": 1}, tr("привет", lang="ru"))])
    for f in frames(np.concatenate([tone(1.5, 0.3), silence(1.0), tone(1.5, 0.5), silence(1.0)]), Channel.MIC):
        p.process(f)
    p.finish()
    assert [(l.speaker, l.text) for l in read_lines(w.dir)] == [(Speaker.ME, "hi I'm Roman"), (Speaker.OTHER, "привет")]


def test_silence_produces_no_lines(tmp_path):  # Review Focus 1
    p, w = make(tmp_path, [])
    for f in frames(silence(30.0), Channel.MIC):
        p.process(f)
    p.finish()
    assert read_lines(w.dir) == []


def test_hallucinated_and_echo_lines_dropped(tmp_path):
    p, w = make(
        tmp_path,
        [
            ({"en": 1}, tr("we ship the release on friday")),  # Them
            ({"en": 1}, tr("we ship the release on friday")),  # mic echo
            ({"en": 1}, tr("Thanks for watching!")),
        ],
    )
    sys_ = frames(np.concatenate([tone(2.0, 0.3), silence(1.0)]), Channel.SYSTEM)
    mic = frames(np.concatenate([tone(2.0, 0.5), silence(1.0), tone(1.5, 0.5), silence(1.0)]), Channel.MIC)
    for f in by_time(sys_ + mic):
        p.process(f)
    p.finish()
    assert [l.speaker for l in read_lines(w.dir)] == [Speaker.THEM]


def test_on_line_callback_and_latency_recorded(tmp_path):
    seen = []
    p, w = make(tmp_path, [({"en": 1}, tr("one")), ({"en": 1}, tr("two"))], on_line=seen.append)
    for f in frames(np.concatenate([tone(1.5), silence(1.0), tone(1.5), silence(1.0)]), Channel.MIC):
        p.process(f)
    p.finish()
    assert [l.text for l in seen] == ["one", "two"] and len(p.latencies) == 2
    assert all(x >= 0 for x in p.latencies)


def test_text_is_stripped(tmp_path):
    p, w = make(tmp_path, [({"en": 1}, tr("  padded text "))])
    for f in frames(np.concatenate([tone(1.5), silence(1.0)]), Channel.MIC):
        p.process(f)
    p.finish()
    assert read_lines(w.dir)[0].text == "padded text"
