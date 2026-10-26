"""Engine configuration (defaults + optional TOML overrides)."""
from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    tau: float = 0.55
    vad_threshold: float = 0.5
    pre_pad_ms: int = 200
    end_silence_ms: int = 600
    max_utt_s: float = 15.0
    split_search_s: float = 3.0
    split_window_ms: int = 100
    allowed_langs: tuple[str, ...] = ("en", "nl", "uk")  # add "ru" only if you want Russian output
    lang_tie_margin: float = 0.35  # decodes within this avg-logprob of the best count as a tie
    whisper_model: str = "mlx-community/whisper-large-v3-turbo"  # live
    final_whisper_model: str = "mlx-community/whisper-large-v3-mlx"  # saved transcript (slower, more accurate)
    vocabulary: tuple[str, ...] = ()  # names / course terms that Whisper should spell right
    post_pad_ms: int = 200
    context_lines: int = 2
    short_utt_s: float = 1.0
    neighbor_gap_s: float = 1.5
    echo_containment: float = 0.8
    echo_min_tokens: int = 3
    echo_window_s: float = 1.0
    echo_overlap: float = 0.5  # mic speech (not you) that overlaps call audio this much is speaker echo
    my_names: tuple[str, ...] = ("Roman", "Roma", "Роман", "Романе", "Рома", "Ромо", "Ромчик")
    suggest_delay_s: float = 0.8  # wait for the question to finish before suggesting an answer
    suggest_min_gap_s: float = 15.0  # between automatic suggestions for plain questions (names always trigger)
    suggest_context_lines: int = 40
    project_tools: tuple[str, ...] = ("Read", "Grep", "Glob")  # read-only tools inside the session's project
    project_commands: tuple[str, ...] = ("git log", "git show", "git diff", "git status", "git branch", "ls")  # read-only shell
    connector_max_turns: int = 10  # with connectors (Jira, Gmail, …) Claude may need a few lookups
    project_max_turns: int = 6
    collect_my_speech: bool = True  # keep your own lines (app support/my_speech.jsonl) so replies sound like you
    style_samples: int = 25  # how many of your lines go into a reply prompt as style examples
    sessions_dir: Path = field(default_factory=lambda: Path("~/Desktop/Work/OnTrack/sessions").expanduser())
    app_support_dir: Path = field(
        default_factory=lambda: Path("~/Library/Application Support/OnTrack").expanduser()
    )
    notes_dir: Path = field(default_factory=lambda: Path("~/Desktop/Work/OnTrack/notes").expanduser())
    llm_model: str = "sonnet"  # end-of-session summary
    fast_llm_model: str = "haiku"  # live questions and suggested replies: fastest model
    claude_path: str = ""
    llm_extra_args: tuple[str, ...] = ("--strict-mcp-config", "--tools", "")  # Q&A only: no tools, no MCP servers
    max_backlog_s: float = 120.0
    qa_timeout_s: float = 60
    summary_timeout_s: float = 300
    fallback_whisper_model: str = "mlx-community/whisper-small-mlx"
    degraded_lag_s: float = 20.0
    mic_hold_max_s: float = 15.0
    gap_marker_s: float = 10.0
    overlay_lines: int = 3
    retranscribe: bool = True
    live_preview: bool = True  # word-by-word grey preview line in the overlay
    preview_interval_s: float = 0.7
    preview_min_s: float = 0.6


def _coerce(f: dataclasses.Field, value):
    default = Config().__getattribute__(f.name)
    if isinstance(default, Path):
        return Path(str(value)).expanduser()
    if isinstance(default, tuple):
        return tuple(value)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{f.name} must be true/false")
        return value
    if isinstance(default, float):
        return float(value)
    if isinstance(default, int):
        return int(value)
    return value


def apply_overrides(cfg: Config, data: dict) -> Config:
    fields = {f.name: f for f in dataclasses.fields(Config)}
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(unknown)}")
    return dataclasses.replace(cfg, **{k: _coerce(fields[k], v) for k, v in data.items()})


def load_config(path: Path | None) -> Config:
    if path is None or not Path(path).exists():
        return Config()
    return apply_overrides(Config(), tomllib.loads(Path(path).read_text(encoding="utf-8")))
