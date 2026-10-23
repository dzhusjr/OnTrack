"""Real-model tests. Mac only: `uv sync --extra models && HF_TOKEN=... uv run pytest -m models -v`.
Fixtures come from `uv run python scripts/make_fixtures.py`."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from ontrack.audio_source import WavAudioSource, load_wav_16k
from ontrack.cli import main
from ontrack.config import load_config
from ontrack.pipeline import Pipeline, real_backends
from ontrack.segmenter import Segmenter
from ontrack.session import SessionWriter, read_lines
from ontrack.speaker import enroll, load_voiceprint
from ontrack.transcriber import Transcriber
from ontrack.types import AudioFrame, Channel, Utterance

pytestmark = pytest.mark.models
FX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def backends():
    return real_backends(load_config(None))


def cfg_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(f'app_support_dir = "{tmp_path / "support"}"\n')
    return p


@pytest.mark.parametrize("stem,word", [("en", "budget"), ("ru", "бюджет"), ("uk", "бюджет")])
def test_whisper_detects_languages(backends, stem, word):
    x = load_wav_16k(FX / f"{stem}.wav")
    t = Transcriber(backends.asr, load_config(None)).transcribe(Utterance(Channel.MIC, 0, len(x) / 16000, x))
    assert t.lang == stem and word in t.text.lower()


def test_codeswitch_gets_two_languages(backends, tmp_path):
    assert main(["transcribe", str(FX / "codeswitch.wav"), "--name", "cs", "--sessions-dir", str(tmp_path / "s"),
                 "--config", str(cfg_file(tmp_path))], backends=backends) == 0
    session = next((tmp_path / "s").iterdir())
    assert [l.lang for l in read_lines(session)] == ["en", "ru"]


def test_me_vs_other_accuracy(backends, tmp_path):
    cfg = cfg_file(tmp_path)
    assert main(["enroll", "--wav", str(FX / "enroll_daniel.wav"), "--config", str(cfg)], backends=backends) == 0
    assert main(["transcribe", str(FX / "dialog.wav"), "--name", "dialog", "--sessions-dir", str(tmp_path / "s"),
                 "--config", str(cfg)], backends=backends) == 0
    truth = (FX / "dialog_truth.txt").read_text().split()
    got = [l.speaker.value for l in read_lines(next((tmp_path / "s").iterdir()))]
    assert len(got) == len(truth), got
    assert np.mean([g == t for g, t in zip(got, truth)]) >= 0.9


def test_latency_budget(backends, tmp_path):
    cfg = replace(load_config(None), sessions_dir=tmp_path, app_support_dir=tmp_path / "support")
    enroll(backends.embedder, load_wav_16k(FX / "enroll_daniel.wav"), cfg.app_support_dir / "voiceprint.npy")
    vp = load_voiceprint(cfg.app_support_dir / "voiceprint.npy")  # embeddings are part of the measured cost
    w = SessionWriter.start(tmp_path, "lat", datetime.now())
    p = Pipeline(cfg, backends, w, vp)
    for f in WavAudioSource(FX / "dialog.wav", Channel.MIC).iter_frames():
        p.process(f)
    p.finish()
    assert p.latencies and float(np.percentile(p.latencies, 90)) <= 2.0, p.latencies


def test_silero_silence(backends):
    seg = Segmenter(Channel.MIC, backends.vad_factory(), load_config(None), origin_ns=0)
    from ontrack.types import AudioFrame
    out = []
    for i in range(500):  # 10 s
        out += seg.push(AudioFrame(Channel.MIC, i * 20_000_000, np.zeros(320, np.float32)))
    assert out == [] and seg.flush() == []


def test_room_noise_produces_no_lines(backends, tmp_path):
    """Pink-ish noise + 50 Hz hum (~ventilation) for 20 s must not produce transcript lines."""
    rng = np.random.default_rng(0)
    white = rng.standard_normal(20 * 16000).astype(np.float32)
    pink = np.cumsum(white) / 400.0
    pink = (pink - np.convolve(pink, np.ones(1600) / 1600, mode="same")).astype(np.float32)
    t = np.arange(20 * 16000) / 16000
    x = (0.02 * pink / (np.abs(pink).max() or 1) + 0.01 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
    w = SessionWriter.start(tmp_path, "noise", datetime.now())
    p = Pipeline(replace(load_config(None), sessions_dir=tmp_path), backends, w, None)
    for i in range(0, len(x), 320):
        p.process(AudioFrame(Channel.MIC, i * 62_500, x[i : i + 320]))
    p.finish()
    assert read_lines(w.dir) == []
