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
from .session import paused_ranges
from .naming import is_auto_named, rename_notes, summary_title
from .summarize import summarize

log = logging.getLogger(__name__)
ACTIONS = ("save_all", "transcript_only", "discard")
SESSION_ID_FILE = ".session_id"  # in each notes folder: which session (and kept audio) it came from


class ProcessingDeferred(Exception):
    """Stop at a safe boundary, keeping the recording for a later retry."""


class InterruptibleTranscriber:
    def __init__(self, transcriber, check):
        self.transcriber, self.check = transcriber, check

    def transcribe(self, utterance):
        self.check()
        result = self.transcriber.transcribe(utterance)
        self.check()
        return result


@dataclass
class FinalizeDeps:
    diarizer: object
    embedder: object
    voiceprint: np.ndarray | None
    transcriber: object | None
    runner: object
    cfg: Config
    vad_factory: object | None = None  # for transcribing stretches where live transcription was paused
    projects: tuple[str, ...] = ()  # project names under the session's work folder (for the session title)
    cancelled: Callable[[], bool] = lambda: False

    def check_cancelled(self):
        if self.cancelled():
            raise ProcessingDeferred()


def _copy_if(src: Path, dst: Path) -> None:
    if src.exists() and src.read_text(encoding="utf-8").strip():
        shutil.copyfile(src, dst)


def finalize(
    session_dir: Path,
    audio_dir: Path,
    action: str,
    deps: FinalizeDeps,
    progress: Callable[[str, float], None],
    on_summary_error: Callable[[LlmError], None] | None = None,
    on_warning: Callable[[str], None] | None = None,
    improve: bool = True,
) -> Path | None:
    """improve=False keeps the live transcript (fast, light on battery) and keeps the audio, so the session can
    be improved later from the Notes window. Stretches recorded with live transcription off are always transcribed."""
    if action not in ACTIONS:
        raise ValueError(f"unknown finalize action {action!r}")
    session_dir, audio_dir = Path(session_dir), Path(audio_dir)
    if action == "discard":
        delete_audio(audio_dir)
        shutil.rmtree(session_dir, ignore_errors=True)
        progress("done", 1.0)
        return None

    deps.check_cancelled()
    heavy = improve or bool(paused_ranges(session_dir))
    if heavy:
        _postprocess(session_dir, audio_dir, deps, progress, on_warning)

    deps.check_cancelled()
    notes = deps.cfg.notes_dir / session_dir.name
    notes.mkdir(parents=True, exist_ok=True)
    if action == "save_all":
        progress("summarizing", 0.7)
        try:
            summary = summarize(deps.runner, session_dir, deps.cfg, deps.projects)
            title = summary_title(summary)
            if title and is_auto_named(session_dir):  # quick-started session: name it "Daily 2026-10-27 — Search API"
                notes = rename_notes(notes, title)
            (notes / "summary.md").write_text(summary, encoding="utf-8")
            (notes / "summary_pending").unlink(missing_ok=True)
        except LlmError as e:
            log.warning("summary failed (%s): %s", e.code, e.message)
            (notes / "summary_pending").write_text(e.code, encoding="utf-8")
            if on_summary_error:
                on_summary_error(e)

    progress("saving", 0.9)
    final = session_dir / "transcript.final.md"
    shutil.copyfile(final if final.exists() else session_dir / "transcript.md", notes / "transcript.md")
    _copy_if(session_dir / "qa.md", notes / "qa.md")
    _copy_if(session_dir / "notes.md", notes / "notes.md")
    (notes / ".pending_sync").touch()
    (notes / SESSION_ID_FILE).write_text(session_dir.name, encoding="utf-8")
    deps.check_cancelled()
    (session_dir / ".pending_processing").unlink(missing_ok=True)
    if heavy:
        delete_audio(audio_dir)  # otherwise kept for "Improve Transcript" later
    progress("done", 1.0)
    return notes


def _postprocess(session_dir: Path, audio_dir: Path, deps: FinalizeDeps, progress, on_warning) -> None:
    def report(stage, pct):
        deps.check_cancelled()
        progress(stage, 0.05 + 0.6 * pct)

    transcriber = InterruptibleTranscriber(deps.transcriber, deps.check_cancelled) if deps.transcriber else None
    try:
        deps.check_cancelled()
        postprocess(session_dir, audio_dir, deps.diarizer, deps.embedder, deps.voiceprint, transcriber,
                    deps.cfg, progress=report, on_warning=on_warning,
                    vad_factory=deps.vad_factory)
    except ProcessingDeferred:
        raise
    except Exception as e:  # never lose the transcript because post-processing broke
        log.exception("post-processing failed: %s", e)


def improve_saved(session_dir: Path, audio_dir: Path, notes: Path, deps: FinalizeDeps,
                  progress: Callable[[str, float], None], on_warning: Callable[[str], None] | None = None) -> Path:
    """Later 'Improve Transcript': speaker separation + accurate model on the kept audio; replaces transcript.md."""
    session_dir, audio_dir, notes = Path(session_dir), Path(audio_dir), Path(notes)
    if not audio_dir.exists():
        raise FileNotFoundError("The audio of this session is gone (it is deleted once a transcript is improved).")
    _postprocess(session_dir, audio_dir, deps, progress, on_warning)
    progress("saving", 0.9)
    final = session_dir / "transcript.final.md"
    shutil.copyfile(final if final.exists() else session_dir / "transcript.md", notes / "transcript.md")
    (notes / ".pending_sync").touch()
    deps.check_cancelled()
    (session_dir / ".pending_processing").unlink(missing_ok=True)
    delete_audio(audio_dir)
    progress("done", 1.0)
    return notes


def notes_dir_for(notes_root: Path, session_id: str) -> Path:
    """Saved notes of a session (their folder may have been renamed to e.g. ...-daily-search-api)."""
    plain = Path(notes_root) / session_id
    if plain.exists():
        return plain
    for marker in Path(notes_root).glob(f"*/{SESSION_ID_FILE}"):
        try:
            if marker.read_text(encoding="utf-8").strip() == session_id:
                return marker.parent
        except OSError:
            continue
    return plain


def retry_summary(notes: Path, session_dir: Path, runner, cfg: Config) -> None:
    summary = summarize(runner, session_dir, cfg)
    (notes / "summary.md").write_text(summary, encoding="utf-8")
    (notes / "summary_pending").unlink(missing_ok=True)
    (notes / ".pending_sync").touch()
