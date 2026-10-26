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


def build_qa_prompt(transcript_md: str, notes_md: str, question: str, me: str = "Roman",
                    project: str | None = None) -> str:
    project_hint = (f"You are inside {me}'s project folder \"{project}\": read files there when the question is "
                    "about the project or its code.\n") if project else ""
    return (
        f"You are OnTrack, {me}'s live assistant during a lecture, meeting or conversation.\n"
        f"Answer his question using the live transcript below{' and the project' if project else ''}.\n"
        "Be brief: at most 3 short sentences or 4 short bullets. No preamble, no restating the question.\n"
        "The transcript is live speech recognition: silently read through misheard words and duplicates; never "
        "comment on transcript quality. Only if the specific answer is not there, say so in a few words.\n"
        "Add a session time like [00:12:03] only when it helps find the moment.\n"
        + project_hint +
        f"Speakers: Me = {me}, Other/Speaker X = people in the room, Them = people on the call.\n"
        "Answer in the same language as the question.\n\n"
        f"<transcript>\n{transcript_md.strip()}\n</transcript>\n\n"
        f"<my_notes>\n{notes_md.strip()}\n</my_notes>\n\n"
        f"Question: {question.strip()}\n"
    )


def _stream(runner, prompt: str, timeout_s: float, project_dir: Path | None):
    """Pass the project folder only when there is one (test fakes and old runners take two arguments)."""
    if project_dir:
        return runner.stream(prompt, timeout_s, project_dir=project_dir)
    return runner.stream(prompt, timeout_s)


class QaService:
    def __init__(self, runner, cfg: Config) -> None:
        self.runner = runner
        self.cfg = cfg
        self._lock = threading.Lock()  # one question in flight; others wait their turn

    def ask(self, session_dir: Path, question: str, now: datetime | None = None,
            project_dir: Path | None = None) -> Iterator[str]:
        session_dir = Path(session_dir)
        now = now or datetime.now()
        me = self.cfg.my_names[0] if self.cfg.my_names else "Roman"
        with self._lock:
            prompt = build_qa_prompt(_read(session_dir / "transcript.md"), _read(session_dir / "notes.md"), question,
                                     me=me, project=Path(project_dir).name if project_dir else None)
            parts: list[str] = []
            try:
                for chunk in _stream(self.runner, prompt, self.cfg.qa_timeout_s, project_dir):
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
