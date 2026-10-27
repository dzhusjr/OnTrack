"""How you speak: every `Me` line is collected (locally, never in the repo) so suggested replies sound like you."""
from __future__ import annotations

import json
import threading
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

from .types import Line, Speaker

MIN_WORDS = 4  # shorter lines ("так", "окей") say little about style
_KEEP = 3000


class SpeechStyle:
    def __init__(self, path: Path, sessions_dir: Path | None = None) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._lines: deque[tuple[str, str]] = deque(maxlen=_KEEP)  # (lang, text)
        self._seen: set[str] = set()
        if self.path.exists():
            self._load()
        elif sessions_dir is not None:
            self._backfill(Path(sessions_dir))

    def _remember(self, lang: str, text: str) -> bool:
        key = " ".join(text.lower().split())
        if len(text.split()) < MIN_WORDS or key in self._seen:
            return False
        self._seen.add(key)
        self._lines.append((lang or "", text.strip()))
        return True

    def _load(self) -> None:
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(raw)
                self._remember(d.get("lang", ""), d.get("text", ""))
            except (ValueError, AttributeError):
                continue

    def _backfill(self, sessions_dir: Path) -> None:
        """First run: start from your lines in earlier sessions."""
        rows = []
        for jl in sorted(sessions_dir.glob("*/transcript.jsonl")):
            for raw in jl.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if d.get("speaker") == Speaker.ME.value and self._remember(d.get("lang", ""), d.get("text", "")):
                    rows.append({"t": "", "lang": d.get("lang", ""), "text": d.get("text", "").strip()})
        if rows:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    def add(self, line: Line) -> None:
        if line.speaker != Speaker.ME:
            return
        with self._lock:
            if not self._remember(line.lang, line.text):
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"), "lang": line.lang,
                                    "text": line.text.strip()}, ensure_ascii=False) + "\n")

    def samples(self, lang: str | None, n: int) -> list[str]:
        """Your most recent lines, in the conversation's language when there are enough of them."""
        with self._lock:
            same = [t for l, t in self._lines if l == lang]
            pool = same if len(same) >= min(n, 5) else [t for _, t in self._lines]
            return pool[-n:] if n > 0 else []


def conversation_language(lines: list[Line], window: int = 20) -> str | None:
    """Language most of the recent talk is in (by characters)."""
    counts: Counter[str] = Counter()
    for l in [l for l in lines if l.speaker != Speaker.NOTE][-window:]:
        if l.lang:
            counts[l.lang] += len(l.text)
    return counts.most_common(1)[0][0] if counts else None


def style_block(me: str, language: str | None, examples: list[str]) -> str:
    lang = f"Reply in {language} (the language of the conversation), always.\n" if language else \
        "Reply in the language of the conversation.\n"
    ex = "\n".join(f"- {e}" for e in examples)
    how = (f"Sound like {me}: reuse their word choice, sentence length and the way they open and phrase things, "
           f"from these real lines of them speaking (speech recognition, may contain errors — copy the style, not "
           f"the content):\n<how_they_speak>\n{ex}\n</how_they_speak>\n") if examples else ""
    return (lang + "Tone: polite and professional (fairly formal), unless the conversation is clearly casual.\n" + how)
