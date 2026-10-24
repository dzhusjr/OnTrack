from dataclasses import replace

import numpy as np
import pytest
from fakes import ME_VP, FakeEmbedder, tone, utt

from ontrack.config import load_config
from ontrack.speaker import SpeakerLabeler, cosine, enroll, load_voiceprint
from ontrack.types import Channel, Speaker


def test_system_channel_is_them_without_embedding():
    emb = FakeEmbedder()
    lab = SpeakerLabeler(emb, ME_VP, load_config(None))
    assert lab.label(utt(Channel.SYSTEM, amp=0.3)) == Speaker.THEM and emb.calls == 0


def test_mic_matches_voiceprint_is_me_else_other():
    lab = SpeakerLabeler(FakeEmbedder(), ME_VP, load_config(None))
    assert lab.label(utt(Channel.MIC, 0.0, 2.0, amp=0.3)) == Speaker.ME
    assert lab.label(utt(Channel.MIC, 5.0, 7.0, amp=0.5)) == Speaker.OTHER


def test_threshold_from_config():
    lab = SpeakerLabeler(FakeEmbedder(), np.array([0.5, 0.866, 0]), replace(load_config(None), tau=0.6))
    assert lab.label(utt(Channel.MIC, 0, 2, amp=0.3)) == Speaker.OTHER  # cos = 0.5 < 0.6


def test_no_voiceprint_labels_mic_as_me():
    lab = SpeakerLabeler(FakeEmbedder(), None, load_config(None))
    assert not lab.has_voiceprint and lab.label(utt(Channel.MIC, amp=0.5)) == Speaker.ME


def test_short_utterance_inherits_recent_neighbour():
    emb = FakeEmbedder()
    lab = SpeakerLabeler(emb, ME_VP, load_config(None))
    lab.label(utt(Channel.MIC, 0.0, 2.0, amp=0.5))  # Other
    assert lab.label(utt(Channel.MIC, 3.0, 3.6, amp=0.3)) == Speaker.OTHER and emb.calls == 1
    assert lab.label(utt(Channel.MIC, 6.0, 6.5, amp=0.3)) == Speaker.ME  # gap 2.4 s > 1.5 s → embeds


def test_cosine_handles_scale():
    assert cosine(np.array([2.0, 0, 0]), np.array([1.0, 0, 0])) == pytest.approx(1.0)


def test_enroll_writes_normalized_voiceprint(tmp_path):
    score = enroll(FakeEmbedder(), tone(10.0, amp=0.3), tmp_path / "vp.npy")
    vp = load_voiceprint(tmp_path / "vp.npy")
    assert score == pytest.approx(1.0) and np.linalg.norm(vp) == pytest.approx(1.0)


def test_enroll_too_short(tmp_path):
    with pytest.raises(ValueError):
        enroll(FakeEmbedder(), tone(5.0), tmp_path / "vp.npy")


def test_load_voiceprint_missing_is_none(tmp_path):
    assert load_voiceprint(tmp_path / "nope.npy") is None


def test_enroll_ignores_silent_windows(tmp_path):  # review: a voiceprint made from silence matches nobody
    x = np.concatenate([np.zeros(6 * 16000, np.float32), tone(9.0, amp=0.3)])
    score = enroll(FakeEmbedder(), x, tmp_path / "vp.npy")
    assert score == pytest.approx(1.0)
    assert np.allclose(load_voiceprint(tmp_path / "vp.npy"), [1.0, 0, 0])


def test_enroll_rejects_silence(tmp_path):
    with pytest.raises(ValueError, match="quiet"):
        enroll(FakeEmbedder(), np.zeros(30 * 16000, np.float32), tmp_path / "vp.npy")


def test_enroll_accepts_quiet_but_real_speech(tmp_path):  # MacBook mic, normal voice, pauses: low RMS but not silence
    x = tone(30.0, amp=0.003)  # rms ≈ 0.002
    from fakes import FakeEmbedder as E

    class AnyEmb(E):
        def embed(self, samples):
            self.calls += 1
            return np.array([1.0, 0.0, 0.0])

    assert enroll(AnyEmb(), x, tmp_path / "vp.npy") == pytest.approx(1.0)


def test_enroll_error_reports_level(tmp_path):
    with pytest.raises(ValueError, match=r"level 0\.0000"):
        enroll(FakeEmbedder(), np.zeros(30 * 16000, np.float32), tmp_path / "vp.npy")
