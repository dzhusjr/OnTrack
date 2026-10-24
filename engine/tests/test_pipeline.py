from dataclasses import replace

import pytest

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


def test_lazy_defers_construction_until_first_use():  # review #4
    from ontrack.pipeline import Lazy

    built = []

    class Thing:
        def embed(self, x):
            return "ok"

    lz = Lazy(lambda: built.append(1) or Thing())
    assert built == []
    assert lz.embed(None) == "ok" and lz.embed(None) == "ok"
    assert built == [1]


def test_resumed_session_keeps_original_time_origin(tmp_path):  # review #7
    cfg = replace(load_config(None), sessions_dir=tmp_path)
    w = SessionWriter.start(tmp_path, "T", NOW)
    p = Pipeline(cfg, Backends(EnergyVad, FakeAsr([({"en": 1}, tr("a"))]), FakeEmbedder()), w, None)
    for f in frames(np.concatenate([tone(1.5), silence(1.0)]), Channel.MIC, start_ns=5_000_000_000):
        p.process(f)
    p.finish()
    r = SessionWriter.resume(w.dir)
    assert r.origin_ns == 5_000_000_000
    p2 = Pipeline(cfg, Backends(EnergyVad, FakeAsr([({"en": 1}, tr("b"))]), FakeEmbedder()), r, None)
    for f in frames(np.concatenate([tone(1.5), silence(1.0)]), Channel.MIC, start_ns=65_000_000_000):
        p2.process(f)
    p2.finish()
    assert read_lines(w.dir)[-1].t_start == pytest.approx(60.0, abs=0.25)


def test_mic_fragment_during_open_them_utterance_is_dropped_as_echo(tmp_path):  # M2 carry-over
    p, w = make(
        tmp_path,
        [
            ({"en": 1}, tr("we should ship the release on friday morning")),  # Them (emitted at ~6.6 s)
            ({"en": 1}, tr("ship the release on friday")),  # mic fragment 1
            ({"en": 1}, tr("release on friday morning")),  # mic fragment 2
        ],
    )
    sys_ = frames(np.concatenate([tone(6.0, 0.3), silence(1.0)]), Channel.SYSTEM)
    mic = frames(np.concatenate([silence(1.0), tone(1.5, 0.5), silence(1.0), tone(1.5, 0.5), silence(2.0)]), Channel.MIC)
    for f in by_time(sys_ + mic):
        p.process(f)
    p.finish()
    assert [l.speaker for l in read_lines(w.dir)] == [Speaker.THEM]


def test_mic_only_session_not_delayed(tmp_path):
    seen = []
    p, w = make(tmp_path, [({"en": 1}, tr("hello"))], on_line=seen.append)
    for f in frames(np.concatenate([tone(1.5), silence(1.0)]), Channel.MIC):
        p.process(f)
    assert [l.text for l in seen] == ["hello"]  # before finish()


def test_hold_released_after_max(tmp_path):
    cfg = replace(load_config(None), sessions_dir=tmp_path, mic_hold_max_s=3.0)
    w = SessionWriter.start(tmp_path, "T", NOW)
    asr = FakeAsr([({"en": 1}, tr("my own words here")), ({"en": 1}, tr("long talk from the call"))])
    p = Pipeline(cfg, Backends(EnergyVad, asr, FakeEmbedder()), w, ME_VP)
    sys_ = frames(np.concatenate([tone(10.0, 0.3), silence(1.0)]), Channel.SYSTEM)
    mic = frames(np.concatenate([silence(0.5), tone(1.5, 0.5), silence(9.0)]), Channel.MIC)
    for f in by_time(sys_ + mic):
        p.process(f)
    p.finish()
    assert [l.speaker for l in read_lines(w.dir)] == [Speaker.OTHER, Speaker.THEM]


def test_degraded_switches_backend(tmp_path):
    cfg = replace(load_config(None), sessions_dir=tmp_path)
    w = SessionWriter.start(tmp_path, "T", NOW)
    main_asr, small_asr = FakeAsr([]), FakeAsr([({"en": 1}, tr("fast"))])
    p = Pipeline(cfg, Backends(EnergyVad, main_asr, FakeEmbedder()), w, None, fallback_asr=small_asr)
    p.set_degraded(True)
    assert p.degraded
    for f in frames(np.concatenate([tone(1.5), silence(1.0)]), Channel.MIC):
        p.process(f)
    assert small_asr.calls and not main_asr.calls
    p.set_degraded(False)
    assert p.transcriber.backend is main_asr


def test_mic_released_when_system_audio_stalls(tmp_path):  # review M2 #4
    seen = []
    cfg = replace(load_config(None), sessions_dir=tmp_path, mic_hold_max_s=3.0)
    w = SessionWriter.start(tmp_path, "T", NOW)
    p = Pipeline(cfg, Backends(EnergyVad, FakeAsr([({"en": 1}, tr("still hear you"))]), FakeEmbedder()), w, ME_VP, seen.append)
    for f in frames(silence(1.0), Channel.SYSTEM):  # system stream starts, then stops forever
        p.process(f)
    for f in frames(np.concatenate([tone(1.5, 0.5), silence(6.0)]), Channel.MIC):
        p.process(f)
    assert [l.text for l in seen] == ["still hear you"]  # released before finish()


def test_repeated_short_line_is_dropped(tmp_path):  # "See you soon." twice in a row from noise
    p, w = make(tmp_path, [({"en": 1}, tr("see you soon")), ({"en": 1}, tr("see you soon"))])
    for f in frames(np.concatenate([tone(1.0), silence(1.0), tone(1.0), silence(1.0)]), Channel.MIC):
        p.process(f)
    p.finish()
    assert [l.text for l in read_lines(w.dir)] == ["see you soon"]
