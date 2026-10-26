"""Live follow-up questions about the running session (spec §4.2 qa)."""
from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from .config import Config
from .llm import LlmError


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


_QA_BLOCK = re.compile(r"^## Q \(\d\d:\d\d\) (.+?)\n\n\*\*A:\*\* (.*?)(?=\n## |\Z)", re.S | re.M)


def recent_exchanges(qa_md: str, n: int = 4) -> list[tuple[str, str]]:
    """Last n question/answer pairs from qa.md (unanswered ones skipped), oldest first."""
    pairs = [(q.strip(), a.strip()) for q, a in _QA_BLOCK.findall(qa_md)]
    return [(q, a) for q, a in pairs if a and not a.startswith("_(unanswered")][-n:]


def build_qa_prompt(transcript_md: str, notes_md: str, question: str, me: str = "Roman",
                    project: str | None = None, history: list[tuple[str, str]] | None = None) -> str:
    earlier = "".join(f"Q: {q}\nA: {a}\n\n" for q, a in (history or []))
    earlier_block = (f"<earlier_questions>\n{earlier.strip()}\n</earlier_questions>\n"
                     "The new question may be a follow-up to these: resolve \"it\", \"that\", \"and X?\" from them.\n\n"
                     ) if earlier else ""
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
        + earlier_block +
        f"Question: {question.strip()}\n"
    )


def fast_stream(runner, cfg: Config, prompt: str, timeout_s: float, project_dir: Path | None = None):
    """Live answers use the fastest model; the project folder is passed only when set."""
    kwargs = {"model": cfg.fast_llm_model} if cfg.fast_llm_model else {}
    if project_dir:
        kwargs["project_dir"] = project_dir
    return runner.stream(prompt, timeout_s, **kwargs)


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
            notes = "\n\n".join(t for t in (_read(session_dir / "summary.md"), _read(session_dir / "notes.md")) if t.strip())
            prompt = build_qa_prompt(_read(session_dir / "transcript.md"), notes, question,
                                     me=me, project=Path(project_dir).name if project_dir else None,
                                     history=recent_exchanges(_read(session_dir / "qa.md")))
            parts: list[str] = []
            try:
                for chunk in fast_stream(self.runner, self.cfg, prompt, self.cfg.qa_timeout_s, project_dir):
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
