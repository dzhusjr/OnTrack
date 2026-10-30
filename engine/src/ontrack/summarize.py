"""End-of-session summary via Claude (spec §4.2 summarize)."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .ai import for_task
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


def build_summary_prompt(transcript: str, notes: str, qa: str, lang: str, me: str = "the user", date: str = "",
                         projects: tuple[str, ...] = ()) -> str:
    language = LANG_NAMES.get(lang, "English")
    known = (f" — pick from these project names if one fits: {', '.join(projects)}; otherwise" if projects else "")
    title = (
        f"Start with exactly ONE title line: `# <Kind> {date or '<YYYY-MM-DD>'} — <Project>`. Kind = the meeting type in "
        "one or two English words (Daily, Standup, Planning, Retro, Review, Interview, Lecture, 1:1, Call, Meeting). "
        f"Project = what it was about{known} a 1–3 word topic. Example: `# Daily 2026-10-27 — Search API`.\n"
    )
    return (
        f"You are OnTrack. Write {me}'s notes for the session transcript below, in Markdown, in "
        f"{language} (keep quotes and technical terms in their original language).\n"
        + title +
        "Then use exactly these sections:\n"
        "## TL;DR (2-3 sentences)\n## Key points\n## Decisions\n## Action items (with owner: Me / name / Them)\n"
        "## Open questions\n## Glossary (terms, names, formulas worth remembering)\n"
        "Write 'None' under a section that has nothing. Do not invent content that is not in the transcript.\n"
        f"Speakers: Me = {me}, Other/Speaker X = people in the room, Them = people on the call.\n\n"
        f"<transcript>\n{transcript.strip()}\n</transcript>\n\n"
        f"<my_notes>\n{notes.strip()}\n</my_notes>\n\n"
        f"<questions_asked_during_session>\n{qa.strip()}\n</questions_asked_during_session>\n"
    )


def summarize(runner, session_dir: Path, cfg: Config, projects: tuple[str, ...] = ()) -> str:
    from .naming import session_date

    session_dir = Path(session_dir)
    transcript = _read(session_dir / "transcript.final.md") or _read(session_dir / "transcript.md")
    prompt = build_summary_prompt(
        transcript, _read(session_dir / "notes.md"), _read(session_dir / "qa.md"), dominant_language(session_dir),
        me=cfg.my_names[0] if cfg.my_names else "the user", date=session_date(session_dir), projects=projects,
    )
    text = for_task(runner, "summary").complete(prompt, cfg.summary_timeout_s).strip()
    (session_dir / "summary.md").write_text(text, encoding="utf-8")
    return text
