"""End-of-session summary via Claude (spec §4.2 summarize)."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .config import Config
from .session import read_lines

LANG_NAMES = {"en": "English", "nl": "Dutch", "ru": "Russian", "uk": "Ukrainian"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def dominant_language(session_dir: Path) -> str:
    counts: Counter[str] = Counter()
    for line in read_lines(session_dir):
        counts[line.lang] += len(line.text)
    return counts.most_common(1)[0][0] if counts else "en"


def build_summary_prompt(transcript: str, notes: str, qa: str, lang: str) -> str:
    language = LANG_NAMES.get(lang, "English")
    return (
        "You are OnTrack. Write Roman's notes for the session transcript below, in Markdown, in "
        f"{language} (keep quotes and technical terms in their original language).\n"
        "Use exactly these sections:\n"
        "## TL;DR (2-3 sentences)\n## Key points\n## Decisions\n## Action items (with owner: Me / name / Them)\n"
        "## Open questions\n## Glossary (terms, names, formulas worth remembering)\n"
        "Write 'None' under a section that has nothing. Do not invent content that is not in the transcript.\n"
        "Speakers: Me = Roman, Other/Speaker X = people in the room, Them = people on the call.\n\n"
        f"<transcript>\n{transcript.strip()}\n</transcript>\n\n"
        f"<my_notes>\n{notes.strip()}\n</my_notes>\n\n"
        f"<questions_asked_during_session>\n{qa.strip()}\n</questions_asked_during_session>\n"
    )


def summarize(runner, session_dir: Path, cfg: Config) -> str:
    session_dir = Path(session_dir)
    transcript = _read(session_dir / "transcript.final.md") or _read(session_dir / "transcript.md")
    prompt = build_summary_prompt(
        transcript, _read(session_dir / "notes.md"), _read(session_dir / "qa.md"), dominant_language(session_dir)
    )
    text = runner.complete(prompt, cfg.summary_timeout_s).strip()
    (session_dir / "summary.md").write_text(text, encoding="utf-8")
    return text
