import numpy as np
import pytest
from fakes import frames, tone

from ontrack.recorder import AudioRecorder, delete_audio, load_channel
from ontrack.types import Channel


def test_roundtrip_and_chunk_rollover(tmp_path):
    rec = AudioRecorder(tmp_path / "a", origin_ns=0, chunk_s=1)
    x = tone(2.5, 0.3)
    for f in frames(x, Channel.MIC):
        rec.write(f)
    rec.close()
    files = sorted(p.name for p in (tmp_path / "a").iterdir())
    assert files == ["mic-0000.flac", "mic-0001.flac", "mic-0002.flac"]
    y = load_channel(tmp_path / "a", Channel.MIC)
    assert len(y) == pytest.approx(len(x), abs=320)
    np.testing.assert_allclose(y[: len(x)], x, atol=1e-4)


def test_gap_is_filled_with_silence_to_keep_session_time(tmp_path):
    rec = AudioRecorder(tmp_path / "a", origin_ns=0)
    for f in frames(tone(1.0), Channel.SYSTEM):
        rec.write(f)
    for f in frames(tone(1.0), Channel.SYSTEM, start_ns=3_000_000_000):
        rec.write(f)
    rec.close()
    y = load_channel(tmp_path / "a", Channel.SYSTEM)
    assert len(y) == pytest.approx(4.0 * 16000, abs=320)
    assert np.abs(y[int(1.5 * 16000) : int(2.5 * 16000)]).max() == 0.0


def test_resume_appends_after_existing_audio(tmp_path):
    rec = AudioRecorder(tmp_path / "a", origin_ns=0)
    for f in frames(tone(1.0), Channel.MIC):
        rec.write(f)
    rec.close()
    rec2 = AudioRecorder(tmp_path / "a", origin_ns=0)
    for f in frames(tone(1.0), Channel.MIC, start_ns=2_000_000_000):
        rec2.write(f)
    rec2.close()
    assert len(load_channel(tmp_path / "a", Channel.MIC)) == pytest.approx(3.0 * 16000, abs=320)


def test_missing_channel_is_empty_and_delete(tmp_path):
    assert len(load_channel(tmp_path / "nope", Channel.MIC)) == 0
    rec = AudioRecorder(tmp_path / "a", origin_ns=0)
    for f in frames(tone(0.5), Channel.MIC):
        rec.write(f)
    rec.close()
    delete_audio(tmp_path / "a")
    assert not (tmp_path / "a").exists()
