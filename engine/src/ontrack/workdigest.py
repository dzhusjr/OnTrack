"""What you worked on recently in the project folder — handed to Claude so 'what did you do today?'
gets a concrete, ready-to-read answer instead of a template."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".build", "build", "dist", ".idea", ".next"}
_CACHE_S = 60.0


def _git(repo: Path, *args: str, timeout: float = 4.0) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def find_repos(root: Path, depth: int = 3, limit: int = 30) -> list[Path]:
    """Git repositories at or below root (not inside other repos' ignored folders)."""
    root = Path(root)
    found: list[Path] = []

    def walk(d: Path, level: int) -> None:
        if len(found) >= limit:
            return
        if (d / ".git").exists():
            found.append(d)
            return
        if level >= depth:
            return
        try:
            subdirs = sorted(p for p in d.iterdir() if p.is_dir() and p.name not in _SKIP and not p.name.startswith("."))
        except OSError:
            return
        for p in subdirs:
            walk(p, level + 1)

    walk(root, 0)
    return found


def repo_summary(repo: Path, since: str, max_commits: int) -> str:
    email = _git(repo, "config", "user.email")
    args = ["log", f"--since={since}", "--no-merges", f"-n{max_commits}", "--date=format:%a %H:%M",
            "--pretty=format:%ad  %s"]
    mine = _git(repo, *args, f"--author={email}") if email else ""
    log = mine or _git(repo, *args)
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    changed = [l for l in _git(repo, "status", "--porcelain").splitlines() if l.strip()]
    if not log and not changed:
        return ""
    parts = [f"## {repo.name} (branch {branch or '?'})"]
    if log:
        parts.append(log)
    if changed:
        files = ", ".join(l[3:] for l in changed[:8]) + (" …" if len(changed) > 8 else "")
        parts.append(f"uncommitted ({len(changed)}): {files}")
    return "\n".join(parts)


def recent_files(root: Path, hours: float, limit: int = 12) -> list[str]:
    """Recently edited files outside git repos (notes, docs, reports)."""
    cutoff = time.time() - hours * 3600
    hits: list[tuple[float, str]] = []
    deadline = time.monotonic() + 1.5  # big folders: a partial list beats a slow Reply
    for dirpath, dirnames, filenames in os.walk(root):
        if time.monotonic() > deadline:
            break
        dirnames[:] = [d for d in dirnames if d not in _SKIP and not d.startswith(".")
                       and not (Path(dirpath) / d / ".git").exists()]
        for name in filenames:
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m >= cutoff:
                hits.append((m, str(p.relative_to(root))))
        if len(hits) > 500:
            break
    return [f for _, f in sorted(hits, reverse=True)[:limit]]


class WorkDigest:
    """Cached digest per project folder (git is quick, but not free on every Reply)."""

    def __init__(self, since: str = "36 hours ago", max_commits: int = 12, max_chars: int = 4000) -> None:
        self.since, self.max_commits, self.max_chars = since, max_commits, max_chars
        self._cache: dict[Path, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, project: Path | None) -> str:
        if project is None or not Path(project).is_dir():
            return ""
        project = Path(project)
        with self._lock:
            hit = self._cache.get(project)
            if hit and time.monotonic() - hit[0] < _CACHE_S:
                return hit[1]
        text = self.build(project)
        with self._lock:
            self._cache[project] = (time.monotonic(), text)
        return text

    def warm(self, project: Path | None) -> None:
        """Build in the background when the project is chosen, so the first Reply is instant."""
        if project is not None:
            threading.Thread(target=self.get, args=(project,), daemon=True).start()

    def build(self, project: Path) -> str:
        blocks = [b for b in (repo_summary(r, self.since, self.max_commits) for r in find_repos(project)) if b]
        files = recent_files(project, hours=36)
        if files:
            blocks.append("## recently edited files\n" + "\n".join(files))
        return "\n\n".join(blocks)[: self.max_chars]
