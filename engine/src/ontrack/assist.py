"""Notices when someone addresses Roman and drafts a short reply (live meeting / interview helper)."""
from __future__ import annotations

import re
from pathlib import Path

from .types import Line, Speaker


def name_regex(names: tuple[str, ...]) -> re.Pattern | None:
    names = tuple(n for n in names if n.strip())
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.IGNORECASE | re.UNICODE)


def address_reason(line: Line, names: re.Pattern | None, interview: bool) -> str | None:
    """'name' when someone says your name, 'question' (interview mode) when someone else asks anything."""
    if line.speaker in (Speaker.ME, Speaker.NOTE) or not line.text.strip():
        return None
    if names is not None and names.search(line.text):
        return "name"
    if interview and "?" in line.text:
        return "question"
    return None


def transcript_tail(session_dir: Path, lines: int) -> str:
    p = Path(session_dir) / "transcript.md"
    if not p.exists():
        return ""
    body = [l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("`")]
    return "\n".join(body[-lines:])


def build_suggest_prompt(tail: str, trigger: str, me: str, project: str | None) -> str:
    project_hint = (f"You are inside Roman's project folder \"{project}\"; read files there only if the reply needs "
                    "a concrete fact from it.\n") if project else ""
    return (
        f"You are {me}'s live meeting / interview helper. Someone just addressed {me}:\n"
        f"\"{trigger.strip()}\"\n\n"
        f"Write what {me} could say back, in first person, in the language of that line.\n"
        "At most 2 short sentences, or 3 bullets of a few words each. No preamble, no explanations, "
        "no comments about the transcript. If the line needs no reply, answer just: —\n"
        + project_hint +
        "The transcript is live speech recognition: silently read through misheard words.\n"
        f"Speakers: Me = {me}, Other = people in the room, Them = people on the call.\n\n"
        f"<recent_transcript>\n{tail.strip()}\n</recent_transcript>\n"
    )
