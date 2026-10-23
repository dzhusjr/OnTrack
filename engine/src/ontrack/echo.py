"""Drops mic lines that merely repeat overlapping system-audio speech (spec §4.2)."""
from __future__ import annotations

import re

from .config import Config
from .types import Line, Speaker

_WORD = re.compile(r"\w+", re.UNICODE)
_KEEP_S = 30.0


def tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class EchoFilter:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._recent: list[Line] = []

    def observe(self, line: Line) -> None:
        if line.speaker != Speaker.THEM:
            return
        self._recent.append(line)
        self._recent = [l for l in self._recent if line.t_end - l.t_end <= _KEEP_S]

    def is_echo(self, speaker: Speaker, t_start: float, t_end: float, text: str) -> bool:
        if speaker == Speaker.THEM:
            return False
        mic = set(tokens(text))
        if len(mic) < self.cfg.echo_min_tokens:
            return False
        w = self.cfg.echo_window_s
        for l in self._recent:
            if t_start <= l.t_end + w and t_end >= l.t_start - w:
                if len(mic & set(tokens(l.text))) / len(mic) >= self.cfg.echo_containment:
                    return True
        return False
