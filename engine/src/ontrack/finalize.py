"""End-of-session flow: save summary + transcript / transcript only / discard (spec §7.5)."""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import Config
from .llm import LlmError
from .postprocess import postprocess
from .recorder import delete_audio
from .summarize import summarize

log = logging.getLogger(__name__)
ACTIONS = ("save_all", "transcript_only", "discard")


@dataclass
class FinalizeDeps:
    diarizer: object
    embedder: object
    voiceprint: np.ndarray | None
    transcriber: object | None
    runner: object
    cfg: Config


def _copy_if(src: Path, dst: Path) -> None:
    if src.exists() and src.read_text(encoding="utf-8").strip():
        shutil.copyfile(src, dst)


def finalize(
    session_dir: Path,
    audio_dir: Path,
    action: str,
    deps: FinalizeDeps,
    progress: Callable[[str, float], None],
) -> Path | None:
    if action not in ACTIONS:
        raise ValueError(f"unknown finalize action {action!r}")
    session_dir, audio_dir = Path(session_dir), Path(audio_dir)
    if action == "discard":
        delete_audio(audio_dir)
        shutil.rmtree(session_dir, ignore_errors=True)
        progress("done", 1.0)
        return None

    try:
        postprocess(session_dir, audio_dir, deps.diarizer, deps.embedder, deps.voiceprint, deps.transcriber,
                    deps.cfg, progress=lambda s, p: progress(s, 0.05 + 0.6 * p))
    except Exception as e:  # never lose the transcript because post-processing broke
        log.exception("post-processing failed: %s", e)

    notes = deps.cfg.notes_dir / session_dir.name
    notes.mkdir(parents=True, exist_ok=True)
    if action == "save_all":
        progress("summarizing", 0.7)
        try:
            summary = summarize(deps.runner, session_dir, deps.cfg)
            (notes / "summary.md").write_text(summary, encoding="utf-8")
            (notes / "summary_pending").unlink(missing_ok=True)
        except LlmError as e:
            log.warning("summary failed (%s): %s", e.code, e.message)
            (notes / "summary_pending").write_text(e.code, encoding="utf-8")

    progress("saving", 0.9)
    final = session_dir / "transcript.final.md"
    shutil.copyfile(final if final.exists() else session_dir / "transcript.md", notes / "transcript.md")
    _copy_if(session_dir / "qa.md", notes / "qa.md")
    _copy_if(session_dir / "notes.md", notes / "notes.md")
    (notes / ".pending_sync").touch()
    delete_audio(audio_dir)
    progress("done", 1.0)
    return notes


def retry_summary(notes: Path, session_dir: Path, runner, cfg: Config) -> None:
    summary = summarize(runner, session_dir, cfg)
    (notes / "summary.md").write_text(summary, encoding="utf-8")
    (notes / "summary_pending").unlink(missing_ok=True)
    (notes / ".pending_sync").touch()
