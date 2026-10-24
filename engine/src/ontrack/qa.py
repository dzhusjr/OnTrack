"""Live follow-up questions about the running session (spec §4.2 qa)."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .config import Config
from .llm import LlmError


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_qa_prompt(transcript_md: str, notes_md: str, question: str) -> str:
    return (
        "You are OnTrack, Roman's live assistant during a lecture, meeting or conversation.\n"
        "Answer his question using the live transcript below. Be concise: at most 5 sentences or a short list.\n"
        "Quote session times like [00:12:03] when it helps. If the transcript does not contain the answer, say so.\n"
        "Speakers: Me = Roman, Other/Speaker X = people in the room, Them = people on the call.\n"
        "Answer in the same language as the question.\n\n"
        f"<transcript>\n{transcript_md.strip()}\n</transcript>\n\n"
        f"<my_notes>\n{notes_md.strip()}\n</my_notes>\n\n"
        f"Question: {question.strip()}\n"
    )


class QaService:
    def __init__(self, runner, cfg: Config) -> None:
        self.runner = runner
        self.cfg = cfg
        self._lock = threading.Lock()  # one question in flight; others wait their turn

    def ask(self, session_dir: Path, question: str, now: datetime | None = None) -> Iterator[str]:
        session_dir = Path(session_dir)
        now = now or datetime.now()
        with self._lock:
            prompt = build_qa_prompt(_read(session_dir / "transcript.md"), _read(session_dir / "notes.md"), question)
            parts: list[str] = []
            try:
                for chunk in self.runner.stream(prompt, self.cfg.qa_timeout_s):
                    parts.append(chunk)
                    yield chunk
            except LlmError as e:
                self._append(session_dir, now, question, f"_(unanswered: {e.code})_")
                raise
            self._append(session_dir, now, question, "".join(parts).strip())

    @staticmethod
    def _append(session_dir: Path, now: datetime, question: str, answer: str) -> None:
        with open(session_dir / "qa.md", "a", encoding="utf-8") as f:
            f.write(f"## Q ({now:%H:%M}) {question.strip()}\n\n**A:** {answer}\n\n")
