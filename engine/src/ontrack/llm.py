"""Headless Claude Code (`claude -p`) runner — free via your Claude subscription (spec D6)."""
from __future__ import annotations

import json
import os
import queue
import re
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
    if "unknown option" in t or "unknown argument" in t:
        return "llm_unsupported"
    if "no such file" in t or "command not found" in t:
        return "llm_missing"
    if "rate limit" in t or "429" in t or "usage limit" in t or "overloaded" in t:
        return "llm_rate_limit"
    if "login" in t or "auth" in t or "401" in t or "api key" in t:
        return "llm_auth"
    return "llm_failed"


_MCP_CACHE: dict[str, tuple[float, list[str]]] = {}
_MCP_LINE = re.compile(r"^([^:\s][^:]*?):\s+\S")


def mcp_tool_prefix(name: str) -> str:
    """Claude Code names MCP tools mcp__<server>__<tool>, with the server name sanitised."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name.strip())


def parse_mcp_list(output: str) -> list[str]:
    """Server names from `claude mcp list` ("name: <url or command> - ✓ Connected"); failed ones skipped."""
    names = []
    for line in output.splitlines():
        m = _MCP_LINE.match(line.strip())
        if not m or "fail" in line.lower() or line.lower().startswith(("checking", "no mcp")):
            continue
        names.append(mcp_tool_prefix(m.group(1)))
    return list(dict.fromkeys(names))


def mcp_servers(cfg: Config, ttl_s: float = 600.0) -> list[str]:
    """Connectors configured in Claude Code on this Mac (cached; `claude mcp list` health-checks each one)."""
    key = cfg.claude_path or "claude"
    hit = _MCP_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ttl_s:
        return hit[1]
    exe = find_claude(cfg)
    names: list[str] = []
    if exe is not None:
        try:
            r = subprocess.run([str(exe), "mcp", "list"], capture_output=True, text=True, timeout=30)
            names = parse_mcp_list(r.stdout)
        except (OSError, subprocess.SubprocessError):
            names = []
    _MCP_CACHE[key] = (time.monotonic(), names)
    return names


class ClaudeRunner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _cmd(self, exe: Path, extra: tuple[str, ...], model: str | None = None) -> list[str]:
        return [
            str(exe), "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", model or self.cfg.llm_model, *extra,
        ]

    def project_args(self, connectors: bool = False) -> tuple[str, ...]:
        """Read-only file tools + read-only git/ls inside the project folder. Anything not allowed is refused:
        permission prompts can't be answered in -p mode."""
        tools = tuple(self.cfg.project_tools)
        commands = tuple(f"Bash({c}:*)" for c in self.cfg.project_commands)
        builtin = tools + (("Bash",) if commands else ())
        allow = (*tools, *commands, *self._connector_allow(connectors))
        return (*self._mcp_args(connectors), "--tools", ",".join(builtin), "--allowedTools", *allow, "--max-turns",
                str(self.cfg.connector_max_turns if connectors else self.cfg.project_max_turns))

    def connector_args(self) -> tuple[str, ...]:
        """No project folder, connectors on: no built-in tools, only the user's Claude Code MCP servers."""
        allow = self._connector_allow(True)
        return ("--tools", "", *(("--allowedTools", *allow) if allow else ()),
                "--max-turns", str(self.cfg.connector_max_turns))

    def _mcp_args(self, connectors: bool) -> tuple[str, ...]:
        return () if connectors else ("--strict-mcp-config",)  # strict = ignore every configured MCP server

    def _connector_allow(self, connectors: bool) -> tuple[str, ...]:
        """Permission rules 'mcp__<server>' for every connector Claude Code has (they can't ask in -p mode)."""
        return tuple(f"mcp__{s}" for s in mcp_servers(self.cfg)) if connectors else ()

    def stream(self, prompt: str, timeout_s: float, project_dir: Path | None = None,
               model: str | None = None, connectors: bool = False) -> Iterator[str]:
        project = Path(project_dir).expanduser() if project_dir else None
        if project is not None and not project.is_dir():
            project = None
        if project is not None:
            extra = self.project_args(connectors)
        elif connectors:
            extra = self.connector_args()
        else:
            extra = tuple(self.cfg.llm_extra_args)
        try:
            yield from self._stream(prompt, timeout_s, extra, project, model)
        except LlmError as e:  # noqa: B904
            if e.code != "llm_unsupported" or not extra:
                raise
            # older Claude Code without these flags: retry plain
            yield from self._stream(prompt, timeout_s, (), project, model)

    def _stream(self, prompt: str, timeout_s: float, extra: tuple[str, ...],
                project: Path | None = None, model: str | None = None) -> Iterator[str]:
        exe = find_claude(self.cfg)
        if exe is None:
            raise LlmError("llm_missing", "Claude Code CLI not found — install it and run `claude` once to log in.")
        workdir = str(project) if project is not None else tempfile.mkdtemp(prefix="ontrack-llm-")
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([env.get("PATH", "")] + [os.path.expanduser(p) for p in _EXTRA_PATHS])
        proc = subprocess.Popen(
            self._cmd(exe, extra, model), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
                code = "llm_missing" if proc.returncode == 127 else _classify(err)
                if code == "llm_missing":
                    err += " — set claude_path in OnTrack's engine config to the full path of `claude`."
                raise LlmError(code, err)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if project is None:
                shutil.rmtree(workdir, ignore_errors=True)  # never the user's project folder

    def complete(self, prompt: str, timeout_s: float) -> str:
        return "".join(self.stream(prompt, timeout_s))
