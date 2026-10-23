from datetime import datetime

import pytest
from fakes import NOW, FakeRunner

from ontrack.config import load_config
from ontrack.llm import LlmError
from ontrack.qa import QaService, build_qa_prompt
from ontrack.session import SessionWriter
from ontrack.types import Speaker


def test_prompt_contains_transcript_notes_question_and_language_rule():
    p = build_qa_prompt("`00:00:01` **Them:** deadline is Friday", "my note", "Когда дедлайн?")
    assert "deadline is Friday" in p and "my note" in p and "Когда дедлайн?" in p
    assert "same language as the question" in p


def test_ask_streams_and_appends_qa_md(tmp_path):
    w = SessionWriter.start(tmp_path, "S", NOW)
    w.append(Speaker.THEM, 1, 2, "en", "deadline is Friday")
    runner = FakeRunner([["It is ", "Friday."]])
    qa = QaService(runner, load_config(None))
    assert list(qa.ask(w.dir, "When is the deadline?", now=datetime(2026, 10, 23, 14, 30))) == ["It is ", "Friday."]
    assert "deadline is Friday" in runner.prompts[0]
    assert (w.dir / "qa.md").read_text() == "## Q (14:30) When is the deadline?\n\n**A:** It is Friday.\n\n"


def test_failure_records_unanswered_and_reraises(tmp_path):
    w = SessionWriter.start(tmp_path, "S", NOW)
    qa = QaService(FakeRunner([LlmError("llm_timeout", "slow")]), load_config(None))
    with pytest.raises(LlmError):
        list(qa.ask(w.dir, "Q?", now=datetime(2026, 10, 23, 14, 31)))
    assert "_(unanswered: llm_timeout)_" in (w.dir / "qa.md").read_text()
