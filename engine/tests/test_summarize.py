from fakes import NOW, FakeRunner

from ontrack.config import load_config
from ontrack.session import SessionWriter
from ontrack.summarize import build_summary_prompt, dominant_language, summarize
from ontrack.types import Speaker


def test_dominant_language_by_text_volume(tmp_path):
    w = SessionWriter.start(tmp_path, "S", NOW)
    w.append(Speaker.ME, 0, 1, "en", "ok")
    w.append(Speaker.OTHER, 1, 9, "ru", "Длинное объяснение на русском языке про бюджет")
    assert dominant_language(w.dir) == "ru"


def test_prompt_has_required_sections_and_language():
    p = build_summary_prompt("TRANSCRIPT", "NOTES", "QA", "uk")
    for s in ["TL;DR", "Key points", "Decisions", "Action items", "Open questions", "Glossary", "Ukrainian"]:
        assert s in p
    assert "TRANSCRIPT" in p and "NOTES" in p and "QA" in p


def test_summarize_writes_summary_md_preferring_final_transcript(tmp_path):
    w = SessionWriter.start(tmp_path, "S", NOW)
    w.append(Speaker.ME, 0, 1, "en", "live text")
    (w.dir / "transcript.final.md").write_text("FINAL TEXT")
    runner = FakeRunner([["# Summary\n", "TL;DR: ok"]])
    out = summarize(runner, w.dir, load_config(None))
    assert out == "# Summary\nTL;DR: ok" and (w.dir / "summary.md").read_text() == out
    assert "FINAL TEXT" in runner.prompts[0] and "live text" not in runner.prompts[0]
