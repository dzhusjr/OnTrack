"""Chooses, per request, between Claude and the optional local model.

Claude stays the default and the only thing used unless the user picked a local mode AND loaded the model.
`local_only` never touches Claude: if the local model can't answer, the request fails with a clear error."""
from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from .config import Config
from .llm import LlmError
from .localai import LocalAi

log = logging.getLogger(__name__)

_NO_TOOLS = ("You cannot open files or run tools here. Use only the text in this message; if something is "
             "missing from it, say so briefly.\n\n")


_ONLINE: tuple[float, bool] = (-1e9, True)


def dns_online(host: str = "api.anthropic.com", timeout_s: float = 2.0, ttl_s: float = 10.0) -> bool:
    """Is there a network at all? (A DNS lookup fails at once when offline, so Claude is not left hanging.)"""
    global _ONLINE
    at, ok = _ONLINE
    if time.monotonic() - at < ttl_s:
        return ok
    result: list[bool] = []

    def look() -> None:
        try:
            socket.getaddrinfo(host, 443)
            result.append(True)
        except OSError:
            result.append(False)

    t = threading.Thread(target=look, daemon=True)
    t.start()
    t.join(timeout_s)
    ok = bool(result and result[0])
    _ONLINE = (time.monotonic(), ok)
    return ok


def for_task(runner, task: str):
    """The runner to use for one kind of request ('quick', 'summary', 'project'). Plain runners are returned as is."""
    return runner.for_task(task) if hasattr(runner, "for_task") else runner


class _TaskRunner:
    def __init__(self, router: AiRouter, task: str) -> None:
        self._router, self._task = router, task

    def stream(self, prompt: str, timeout_s: float, **kw) -> Iterator[str]:
        return self._router.stream(prompt, timeout_s, task=self._task, **kw)

    def complete(self, prompt: str, timeout_s: float) -> str:
        return "".join(self.stream(prompt, timeout_s))


class AiRouter:
    def __init__(self, claude, cfg: Config, local: LocalAi) -> None:
        self.claude = claude
        self.cfg = cfg
        self.local = local
        self.online_check = None  # callable -> bool; set by the engine. None = assume there is a network

    def __getattr__(self, name: str):
        """Anything else (e.g. the Claude runner's own attributes) is the Claude runner's."""
        if name == "claude":
            raise AttributeError(name)
        return getattr(self.claude, name)

    def for_task(self, task: str) -> _TaskRunner:
        return _TaskRunner(self, task)

    # `complete`/`stream` keep the ClaudeRunner interface, so code that holds the router directly still works
    def complete(self, prompt: str, timeout_s: float) -> str:
        return "".join(self.stream(prompt, timeout_s, task="summary"))

    def _offline(self) -> bool:
        return self.online_check is not None and not self.online_check()

    def route(self, task: str, connectors: bool) -> tuple[str, str, str]:
        """(provider, model, why). Without a network Claude cannot answer: a loaded local model takes over
        (nothing leaves the Mac), otherwise the error says how to work offline."""
        provider, model, why = self._route(task, connectors)
        if provider == "claude" and self._offline():
            local = self.local
            quick = local.models.get("quick") or ""
            if local.mode != "claude" and local.ready and quick:
                return "local", quick, "offline"
            hint = ("Press Load in Settings → Assistant to use the local model." if local.mode != "claude" else
                    "To work offline, choose Local first or Local only in Settings → Assistant and press Load.")
            raise LlmError("offline", f"No internet, so Claude can't answer. {hint}")
        return provider, model, why

    def _route(self, task: str, connectors: bool) -> tuple[str, str, str]:
        local = self.local
        if local.mode == "claude":
            return "claude", "", "Claude is selected"
        model = local.models.get(task) or ""
        if not model:
            if local.mode == "local_only":
                model = local.models.get("quick") or ""
                if not model:
                    raise LlmError("local_model_missing", "Choose a local model in Settings → Assistant.")
            else:
                return "claude", "", f"no local model chosen for {task}"
        if connectors and local.mode != "local_only":
            return "claude", "", "connectors need Claude"
        if not local.ready:
            if local.mode == "local_only":
                raise LlmError("local_not_loaded",
                               "Local only is on, but the local model is not loaded. Press Load in Settings → Assistant.")
            return "claude", "", "local model not loaded"
        return "local", model, ""

    def stream(self, prompt: str, timeout_s: float, task: str = "quick", **kw) -> Iterator[str]:
        local_only = self.local.mode == "local_only"
        connectors = bool(kw.get("connectors"))
        project = kw.get("project_dir")
        provider, model, why = self.route(task, connectors)
        if provider == "claude":
            self._remember(f"Claude ({why})" if self.local.mode != "claude" else "Claude")
            yield from self.claude.stream(prompt, timeout_s, **kw)
            return
        # local: the project folder is only context text here (no file tools), connectors are not available
        text = (_NO_TOOLS + prompt) if project else prompt
        limit = self.cfg.local_summary_max_tokens if task == "summary" else self.cfg.local_max_tokens
        produced = False
        started = time.monotonic()
        first_s: float | None = None
        try:
            for chunk in self.local.stream(text, model, limit, timeout_s):
                if first_s is None:
                    first_s = time.monotonic() - started
                produced = True
                yield chunk
            self._remember(f"Local · {model.rsplit('/', 1)[-1]}, {first_s or 0:.1f} s to first word")
            return
        except LlmError as e:
            if produced or local_only:
                raise  # a half answer can't be redone elsewhere, and local_only never goes to Claude
            reason = e.message
        if self._offline():
            raise LlmError("local_failed", reason)  # Claude cannot help without a network
        log.warning("local model failed (%s); using Claude", reason)
        self._remember("Claude (fallback: local model failed)")
        yield from self.claude.stream(prompt, timeout_s, **kw)

    def _remember(self, label: str) -> None:
        if self.local.last != label:
            self.local.last = label
            self.local.on_status()
