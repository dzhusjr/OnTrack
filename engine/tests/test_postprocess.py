import numpy as np
from fakes import ME_VP, NOW, EnergyVad, FakeAsr, FakeEmbedder, frames, silence, tone, tr

from ontrack.config import load_config
from ontrack.postprocess import FinalLine, postprocess, read_final_lines
from ontrack.recorder import AudioRecorder
from ontrack.session import SessionWriter
from ontrack.transcriber import Transcriber
from ontrack.types import Channel, Speaker


class FakeDiarizer:
    def __init__(self, segments=None, error=None):
        self.segments, self.error = segments, error

    def diarize(self, samples):
        if self.error:
            raise self.error
        return self.segments


def setup(tmp_path):
    """Mic audio: 0-2 s Alex (0.3), 3-5 s person X (0.5), 6-8 s person Y (0.7); system 9-10 s."""
    w = SessionWriter.start(tmp_path / "sessions", "S", NOW)
    rec = AudioRecorder(tmp_path / "audio", origin_ns=0)
    mic = np.concatenate([tone(2, 0.3), silence(1), tone(2, 0.5), silence(1), tone(2, 0.7), silence(3)])
    for f in frames(mic, Channel.MIC):
        rec.write(f)
    for f in frames(np.concatenate([silence(9), tone(1, 0.3)]), Channel.SYSTEM):
        rec.write(f)
    rec.close()
    w.append(Speaker.ME, 0, 2, "en", "alex live")
    w.append(Speaker.OTHER, 3, 5, "en", "x live")
    w.append(Speaker.OTHER, 6, 8, "en", "y live")
    w.append(Speaker.THEM, 9, 10, "en", "call live")
    w.append(Speaker.NOTE, 10, 10, "", "[gap 00:12]")
    return w


SEGS = [(0.0, 2.0, "SPK_1"), (3.0, 5.0, "SPK_0"), (6.0, 8.0, "SPK_2")]


def test_relabels_mic_lines_with_me_and_letters(tmp_path):
    w = setup(tmp_path)
    out = postprocess(w.dir, tmp_path / "audio", FakeDiarizer(SEGS), FakeEmbedder(), ME_VP, None, load_config(None))
    assert [l.speaker for l in out] == ["Me", "Speaker A", "Speaker B", "Them", "Note"]
    assert [l.text for l in out][:3] == ["alex live", "x live", "y live"]
    assert read_final_lines(w.dir) == out
    md = (w.dir / "transcript.final.md").read_text()
    assert "`00:00:03` **Speaker A:** x live" in md and md.startswith("# S\n")


def test_without_voiceprint_all_clusters_get_letters(tmp_path):
    w = setup(tmp_path)
    out = postprocess(w.dir, tmp_path / "audio", FakeDiarizer(SEGS), FakeEmbedder(), None, None, load_config(None))
    assert [l.speaker for l in out][:3] == ["Speaker A", "Speaker B", "Speaker C"]


def test_diarizer_failure_falls_back_to_live_labels(tmp_path):
    w = setup(tmp_path)
    out = postprocess(w.dir, tmp_path / "audio", FakeDiarizer(error=RuntimeError("no token")), FakeEmbedder(), ME_VP, None, load_config(None))
    assert [l.speaker for l in out] == ["Me", "Other", "Other", "Them", "Note"]


def test_retranscribe_uses_line_audio_span_and_skips_notes(tmp_path):
    w = setup(tmp_path)
    asr = FakeAsr([({"en": 1}, tr(t)) for t in ["alex final", "x final", "y final", "call final"]])
    progress = []
    out = postprocess(w.dir, tmp_path / "audio", FakeDiarizer(SEGS), FakeEmbedder(), ME_VP,
                      Transcriber(asr, load_config(None)), load_config(None), progress=lambda s, p: progress.append(s))
    assert [l.text for l in out] == ["alex final", "x final", "y final", "call final", "[gap 00:12]"]
    assert progress[0] == "separating" and "retranscribing" in progress


def test_missing_audio_keeps_live_transcript(tmp_path):
    w = setup(tmp_path)
    out = postprocess(w.dir, tmp_path / "nothing", FakeDiarizer(SEGS), FakeEmbedder(), ME_VP, None, load_config(None))
    assert [l.speaker for l in out] == ["Me", "Other", "Other", "Them", "Note"]
    assert isinstance(out[0], FinalLine)


def setup_two_speakers_one_line(tmp_path):
    """One live line 0-6 s that actually contains Alex (0-3 s, amp 0.3) then person X (3-6 s, amp 0.5)."""
    w = SessionWriter.start(tmp_path / "sessions", "S", NOW)
    rec = AudioRecorder(tmp_path / "audio", origin_ns=0)
    for f in frames(np.concatenate([tone(3, 0.3), tone(3, 0.5), silence(2)]), Channel.MIC):
        rec.write(f)
    rec.close()
    w.append(Speaker.OTHER, 0, 6, "en", "both speakers merged live")
    return w


def test_final_transcript_is_rebuilt_per_speaker_turn(tmp_path):
    w = setup_two_speakers_one_line(tmp_path)
    asr = FakeAsr([({"en": 1}, tr("i can do the slides")), ({"uk": 1}, tr("також треба забронювати", lang="uk"))])
    segs = [(0.0, 1.4, "S1"), (1.5, 3.0, "S1"), (3.0, 3.2, "S9"), (3.2, 6.0, "S0")]  # S1 split by a short gap; S9 = 0.2 s blip
    out = postprocess(w.dir, tmp_path / "audio", FakeDiarizer(segs), FakeEmbedder(), ME_VP,
                      Transcriber(asr, load_config(None)), load_config(None))
    assert [(l.speaker, l.text, l.lang) for l in out] == [
        ("Me", "i can do the slides", "en"),
        ("Speaker A", "також треба забронювати", "uk"),
    ]
    assert out[0].t_start == 0.0 and out[1].t_start == 3.2


def test_diarization_failure_is_reported(tmp_path):
    w = setup(tmp_path)
    warnings = []
    postprocess(w.dir, tmp_path / "audio", FakeDiarizer(error=RuntimeError("403 gated repo")), FakeEmbedder(), ME_VP,
                None, load_config(None), on_warning=warnings.append)
    assert warnings and "403" in warnings[0]


def setup_paused(tmp_path):
    """Live line at 0-1 s; transcription paused 1.5-9 s while the mic (2-4 s) and a call (6-8 s) were still recorded."""
    w = SessionWriter.start(tmp_path / "sessions", "S", NOW)
    rec = AudioRecorder(tmp_path / "audio", origin_ns=0)
    for f in frames(np.concatenate([tone(1, 0.3), silence(1), tone(2, 0.3), silence(6)]), Channel.MIC):
        rec.write(f)
    for f in frames(np.concatenate([silence(6), tone(2, 0.5), silence(2)]), Channel.SYSTEM):
        rec.write(f)
    rec.close()
    w.append(Speaker.ME, 0, 1, "en", "before pause")
    w.add_paused(1.5, 9.0)
    w.append(Speaker.NOTE, 1.5, 1.5, "", "[live transcription paused 00:07]")
    return w


def test_paused_ranges_are_transcribed_when_saving(tmp_path):
    w = setup_paused(tmp_path)
    asr = FakeAsr([({"en": 1}, tr("before pause final")), ({"en": 1}, tr("said while paused")),
                   ({"en": 1}, tr("call while paused"))])
    out = postprocess(w.dir, tmp_path / "audio", FakeDiarizer(error=RuntimeError("no")), FakeEmbedder(), ME_VP,
                      Transcriber(asr, load_config(None)), load_config(None), vad_factory=EnergyVad)
    got = [(l.speaker, l.text) for l in out]
    assert ("Me", "said while paused") in got and ("Them", "call while paused") in got
    assert [l.t_start for l in out] == sorted(l.t_start for l in out)
    assert [l.seq for l in out] == list(range(len(out)))


def test_paused_call_audio_transcribed_with_speaker_turns(tmp_path):
    w = setup_paused(tmp_path)
    asr = FakeAsr([({"en": 1}, tr("before")), ({"en": 1}, tr("mic turn")), ({"en": 1}, tr("call while paused"))])
    segs = [(0.0, 1.0, "S1"), (2.0, 4.0, "S1")]
    out = postprocess(w.dir, tmp_path / "audio", FakeDiarizer(segs), FakeEmbedder(), ME_VP,
                      Transcriber(asr, load_config(None)), load_config(None), vad_factory=EnergyVad)
    got = [(l.speaker, l.text) for l in out]
    assert ("Me", "mic turn") in got and ("Them", "call while paused") in got
