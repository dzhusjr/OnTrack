"""Readable names for saved sessions: "Daily 2026-10-27 — Search API" → notes/2026-10-27-1043-daily-search-api."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .session import slugify

_AUTO_NAME = re.compile(r"^session( \d{1,2}:\d{2})?$", re.IGNORECASE)  # what the app names a quick start
_TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}")


def is_auto_named(session_dir: Path) -> bool:
    try:
        name = json.loads((Path(session_dir) / "meta.json").read_text(encoding="utf-8")).get("name", "")
    except (OSError, ValueError):
        return False
    return bool(_AUTO_NAME.match(str(name).strip()))


def summary_title(summary: str) -> str | None:
    m = _TITLE.search(summary[:500])
    return m.group(1).strip() if m else None


def session_date(session_dir: Path) -> str:
    """YYYY-MM-DD from the folder name (sessions are named <date>-<HHMM>-<slug>)."""
    m = _PREFIX.match(Path(session_dir).name)
    return m.group(0)[:10] if m else ""


def rename_notes(notes: Path, title: str) -> Path:
    """notes/<date-HHMM>-session-10-43 → notes/<date-HHMM>-daily-search-api (date words are dropped from the slug:
    the folder already starts with the date)."""
    notes = Path(notes)
    m = _PREFIX.match(notes.name)
    if not m:
        return notes
    words = re.sub(r"\d{4}-\d{2}-\d{2}", " ", title)
    slug = slugify(words)
    if not slug or slug == "session":
        return notes
    target = notes.with_name(f"{m.group(0)}-{slug}")
    n = 2
    while target.exists() and target != notes:
        target = notes.with_name(f"{m.group(0)}-{slug}-{n}")
        n += 1
    if target != notes:
        notes.rename(target)
    return target
