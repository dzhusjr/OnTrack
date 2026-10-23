import numpy as np
from fakes import ME_VP, NOW, FakeAsr, FakeEmbedder, FakeRunner, frames, silence, tone, tr

from ontrack.config import load_config
from dataclasses import replace
from ontrack.finalize import FinalizeDeps, finalize, retry_summary
from ontrack.llm import LlmError
from ontrack.recorder import AudioRecorder
from ontrack.session import SessionWriter
from ontrack.transcriber import Transcriber
from ontrack.types import Channel, Speaker


class Diar:
    def diarize(self, samples):
        return [(0.0, 2.0, "S0")]


def setup(tmp_path, runner_script, asr_script=()):
    cfg = replace(load_config(None), notes_dir=tmp_path / "notes", sessions_dir=tmp_path / "sessions")
    w = SessionWriter.start(cfg.sessions_dir, "Lecture", NOW)
    w.append(Speaker.ME, 0, 2, "en", "hello")
    (w.dir / "notes.md").write_text("my scratch note")
    (w.dir / "qa.md").write_text("## Q (14:30) x\n\n**A:** y\n\n")
    audio = tmp_path / "audio" / w.id
    rec = AudioRecorder(audio, origin_ns=0)
    for f in frames(np.concatenate([tone(2, 0.3), silence(1)]), Channel.MIC):
        rec.write(f)
    rec.close()
    deps = FinalizeDeps(
        diarizer=Diar(), embedder=FakeEmbedder(), voiceprint=ME_VP,
        transcriber=Transcriber(FakeAsr(list(asr_script)), cfg) if asr_script else None,
        runner=FakeRunner(runner_script), cfg=cfg,
    )
    return w, audio, deps, cfg


def test_save_all_writes_notes_deletes_audio_marks_sync(tmp_path):
    w, audio, deps, cfg = setup(tmp_path, [["## TL;DR\nGood lecture"]], [({"en": 1}, tr("hello final"))])
    stages = []
    out = finalize(w.dir, audio, "save_all", deps, lambda s, p: stages.append(s))
    assert out == cfg.notes_dir / w.id
    assert (out / "summary.md").read_text() == "## TL;DR\nGood lecture"
    assert "**Me:** hello final" in (out / "transcript.md").read_text()
    assert (out / "qa.md").exists() and (out / "notes.md").read_text() == "my scratch note"
    assert (out / ".pending_sync").exists() and not audio.exists() and w.dir.exists()
    dedup = [s for i, s in enumerate(stages) if i == 0 or stages[i - 1] != s]
    assert dedup == ["separating", "retranscribing", "summarizing", "saving", "done"]


def test_save_all_summary_failure_still_saves_transcript(tmp_path):
    w, audio, deps, cfg = setup(tmp_path, [LlmError("llm_auth", "login")])
    out = finalize(w.dir, audio, "save_all", deps, lambda s, p: None)
    assert (out / "transcript.md").exists() and not (out / "summary.md").exists()
    assert (out / "summary_pending").read_text() == "llm_auth"
    assert not audio.exists()


def test_transcript_only_has_no_summary(tmp_path):
    w, audio, deps, cfg = setup(tmp_path, [])
    out = finalize(w.dir, audio, "transcript_only", deps, lambda s, p: None)
    assert (out / "transcript.md").exists() and not (out / "summary.md").exists()
    assert not (out / "summary_pending").exists() and (out / ".pending_sync").exists()


def test_discard_deletes_audio_and_session(tmp_path):
    w, audio, deps, cfg = setup(tmp_path, [])
    assert finalize(w.dir, audio, "discard", deps, lambda s, p: None) is None
    assert not audio.exists() and not w.dir.exists() and not (cfg.notes_dir / w.id).exists()


def test_retry_summary_after_failure(tmp_path):
    w, audio, deps, cfg = setup(tmp_path, [LlmError("llm_timeout", "slow"), ["## TL;DR\nNow ok"]])
    out = finalize(w.dir, audio, "save_all", deps, lambda s, p: None)
    (out / ".pending_sync").unlink()
    retry_summary(out, w.dir, deps.runner, cfg)
    assert (out / "summary.md").read_text() == "## TL;DR\nNow ok"
    assert not (out / "summary_pending").exists() and (out / ".pending_sync").exists()
