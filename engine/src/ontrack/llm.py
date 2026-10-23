"""Headless Claude Code (`claude -p`) runner — free via Roman's subscription (spec D6)."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from .config import Config

_EXTRA_PATHS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "~/.claude/local", "~/.npm-global/bin")


class LlmError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def find_claude(cfg: Config) -> Path | None:
    if cfg.claude_path:
        p = Path(cfg.claude_path).expanduser()
        if p.exists():
            return p
    path = os.pathsep.join([os.environ.get("PATH", "")] + [os.path.expanduser(p) for p in _EXTRA_PATHS])
    found = shutil.which("claude", path=path)
    return Path(found) if found else None


def _classify(text: str) -> str:
    t = text.lower()
    if "rate limit" in t or "429" in t or "usage limit" in t or "overloaded" in t:
        return "llm_rate_limit"
    if "login" in t or "auth" in t or "401" in t or "api key" in t:
        return "llm_auth"
    return "llm_failed"


class ClaudeRunner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _cmd(self, exe: Path) -> list[str]:
        return [
            str(exe), "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", self.cfg.llm_model, *self.cfg.llm_extra_args,
        ]

    def stream(self, prompt: str, timeout_s: float) -> Iterator[str]:
        exe = find_claude(self.cfg)
        if exe is None:
            raise LlmError("llm_missing", "Claude Code CLI not found — install it and run `claude` once to log in.")
        workdir = tempfile.mkdtemp(prefix="ontrack-llm-")
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([env.get("PATH", "")] + [os.path.expanduser(p) for p in _EXTRA_PATHS])
        proc = subprocess.Popen(
            self._cmd(exe), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=workdir, env=env, text=True, encoding="utf-8",
        )
        lines: queue.Queue[str | None] = queue.Queue()
        stderr_buf: list[str] = []

        def pump_out() -> None:
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        def pump_err() -> None:
            stderr_buf.append(proc.stderr.read())

        threading.Thread(target=pump_out, daemon=True).start()
        threading.Thread(target=pump_err, daemon=True).start()
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except BrokenPipeError:
            pass

        deadline = time.monotonic() + timeout_s
        saw_delta = False
        result_error: str | None = None
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LlmError("llm_timeout", f"Claude did not answer within {timeout_s:.0f} s")
                try:
                    raw = lines.get(timeout=remaining)
                except queue.Empty:
                    continue
                if raw is None:
                    break
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                kind = ev.get("type")
                if kind == "stream_event":
                    delta = (ev.get("event") or {}).get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        saw_delta = True
                        yield delta["text"]
                elif kind == "assistant" and not saw_delta:
                    for block in (ev.get("message") or {}).get("content") or []:
                        if block.get("type") == "text" and block.get("text"):
                            yield block["text"]
                elif kind == "result" and ev.get("is_error"):
                    result_error = str(ev.get("result") or ev.get("subtype") or "error")
            proc.wait(timeout=max(1.0, deadline - time.monotonic()))
            if result_error is not None:
                raise LlmError(_classify(result_error), result_error)
            if proc.returncode != 0:
                time.sleep(0.05)
                err = "".join(stderr_buf).strip() or f"claude exited with {proc.returncode}"
                raise LlmError(_classify(err), err)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            shutil.rmtree(workdir, ignore_errors=True)

    def complete(self, prompt: str, timeout_s: float) -> str:
        return "".join(self.stream(prompt, timeout_s))
