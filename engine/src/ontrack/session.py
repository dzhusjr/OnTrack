"""Crash-safe, resumable session folder writer (spec §4.2 session, §8)."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from .types import Line, Speaker

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def slugify(name: str) -> str:
    return _NON_WORD.sub("-", name.lower()).strip("-")[:40] or "session"


def _hms(t: float) -> str:
    s = int(t)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _md_line(line: Line) -> str:
    return f"`{_hms(line.t_start)}` **{line.speaker.value}:** {line.text}\n"


def _md_header(name: str, started_at: datetime) -> str:
    return f"# {name}\n\n_Started {started_at:%Y-%m-%d %H:%M}_\n\n"


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _valid_prefix(path: Path) -> tuple[list[Line], int]:
    """Parse jsonl; return lines and the byte length of the valid prefix."""
    lines: list[Line] = []
    valid = 0
    if not path.exists():
        return lines, 0
    data = path.read_bytes()
    pos = 0
    for raw in data.split(b"\n"):
        end = pos + len(raw) + 1
        if end > len(data):  # last chunk without newline: partial write
            break
        if raw.strip():
            try:
                lines.append(Line.from_json(raw.decode("utf-8")))
            except (ValueError, KeyError, UnicodeDecodeError):
                break
        valid = end
        pos = end
    return lines, valid


def read_lines(session_dir: Path) -> list[Line]:
    return _valid_prefix(Path(session_dir) / "transcript.jsonl")[0]


class SessionWriter:
    def __init__(self, session_dir: Path, meta: dict, next_seq: int) -> None:
        self._dir = session_dir
        self._meta = meta
        self._next_seq = next_seq
        self._jsonl = open(session_dir / "transcript.jsonl", "a", encoding="utf-8")
        self._md = open(session_dir / "transcript.md", "a", encoding="utf-8")

    @property
    def dir(self) -> Path:
        return self._dir

    @property
    def id(self) -> str:
        return self._dir.name

    @property
    def origin_ns(self) -> int | None:
        return self._meta.get("origin_ns")

    def set_origin(self, origin_ns: int) -> None:
        self._meta["origin_ns"] = origin_ns
        _write_atomic(self._dir / "meta.json", json.dumps(self._meta, ensure_ascii=False, indent=2))

    @property
    def next_seq(self) -> int:
        return self._next_seq

    @classmethod
    def start(cls, root: Path, name: str, now: datetime) -> "SessionWriter":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        base = f"{now:%Y-%m-%d-%H%M}-{slugify(name)}"
        d, n = root / base, 1
        while d.exists():
            n += 1
            d = root / f"{base}-{n}"
        d.mkdir()
        meta = {"id": d.name, "name": name, "started_at": now.isoformat(), "ended_at": None, "version": 1}
        _write_atomic(d / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        (d / "notes.md").write_text("", encoding="utf-8")
        (d / "transcript.jsonl").write_text("", encoding="utf-8")
        (d / "transcript.md").write_text(_md_header(name, now), encoding="utf-8")
        return cls(d, meta, 0)

    @classmethod
    def resume(cls, session_dir: Path) -> "SessionWriter":
        d = Path(session_dir)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        jsonl = d / "transcript.jsonl"
        lines, valid = _valid_prefix(jsonl)
        with open(jsonl, "r+b") as f:
            f.truncate(valid)
        md = _md_header(meta["name"], datetime.fromisoformat(meta["started_at"]))
        _write_atomic(d / "transcript.md", md + "".join(_md_line(l) for l in lines))
        return cls(d, meta, (lines[-1].seq + 1) if lines else 0)

    def append(self, speaker: Speaker, t_start: float, t_end: float, lang: str, text: str) -> Line:
        line = Line(self._next_seq, speaker, t_start, t_end, lang, text)
        self._jsonl.write(line.to_json() + "\n")
        self._jsonl.flush()
        os.fsync(self._jsonl.fileno())
        self._md.write(_md_line(line))
        self._md.flush()
        self._next_seq += 1
        return line

    def close(self, now: datetime) -> None:
        self._meta["ended_at"] = now.isoformat()
        _write_atomic(self._dir / "meta.json", json.dumps(self._meta, ensure_ascii=False, indent=2))
        self._jsonl.close()
        self._md.close()
