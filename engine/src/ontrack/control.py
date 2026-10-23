"""control.sock protocol: newline-delimited JSON (spec §5)."""
from __future__ import annotations

import json

FINALIZE_ACTIONS = ("save_all", "transcript_only", "discard")

# type -> required fields (beyond "type")
REQUIRED: dict[str, tuple[str, ...]] = {
    # app -> engine
    "ping": (),
    "start_session": ("name",),
    "stop_session": (),
    "ask": ("id", "question"),
    "finalize": ("session_id", "action"),
    "retry_summary": ("session_id",),
    "enroll_start": (),
    "set_config": ("config",),
    # engine -> app
    "pong": ("state",),
    "status": ("state", "model_ready", "session_id", "degraded"),
    "line": ("session_id", "seq", "speaker", "t_start", "t_end", "lang", "text"),
    "answer_delta": ("id", "text"),
    "answer_done": ("id",),
    "session_stopped": ("session_id", "duration_s", "line_count"),
    "finalize_progress": ("session_id", "stage", "pct"),
    "finalize_done": ("session_id", "notes_path"),
    "enroll_done": ("score",),
    "error": ("code", "message", "recoverable"),
}


class ProtocolError(ValueError):
    pass


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def validate(msg: dict) -> dict:
    if not isinstance(msg, dict) or "type" not in msg:
        raise ProtocolError("message without type")
    t = msg["type"]
    if t not in REQUIRED:
        raise ProtocolError(f"unknown message type: {t}")
    missing = [f for f in REQUIRED[t] if f not in msg]
    if missing:
        raise ProtocolError(f"{t}: missing {', '.join(missing)}")
    if t == "finalize" and msg["action"] not in FINALIZE_ACTIONS:
        raise ProtocolError(f"finalize: invalid action {msg['action']!r}")
    return msg


class NdjsonDecoder:
    def __init__(self) -> None:
        self._buf = bytearray()
        self.invalid = 0

    def feed(self, data: bytes) -> list[dict]:
        self._buf += data
        out: list[dict] = []
        while (i := self._buf.find(b"\n")) >= 0:
            raw, self._buf = bytes(self._buf[:i]), self._buf[i + 1 :]
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw.decode("utf-8"))
                if not isinstance(msg, dict):
                    raise ValueError
                out.append(msg)
            except (ValueError, UnicodeDecodeError):
                self.invalid += 1
        return out
