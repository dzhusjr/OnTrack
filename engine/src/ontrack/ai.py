"""Chooses, per request, between Claude and the optional local model.

Claude stays the default and the only thing used unless the user picked a local mode AND loaded the model.
`local_only` never touches Claude: if the local model can't answer, the request fails with a clear error."""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from pathlib import Path

from .config import Config
from .llm import LlmError
from .localai import LocalAi

log = logging.getLogger(__name__)

_NO_TOOLS = ("You cannot open files or run tools here. Use only the text in this message; if something is "
             "missing from it, say so briefly.\n\n")


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

    def route(self, task: str, connectors: bool) -> tuple[str, str, str]:
        """(provider, model, why) — provider is 'claude' or 'local'. Raises only in local_only mode."""
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
        log.warning("local model failed (%s); using Claude", reason)
        self._remember("Claude (fallback: local model failed)")
        yield from self.claude.stream(prompt, timeout_s, **kw)

    def _remember(self, label: str) -> None:
        if self.local.last != label:
            self.local.last = label
            self.local.on_status()
