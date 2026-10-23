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
    allowed_langs: tuple[str, ...] = ("en", "ru", "uk")
    whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    context_lines: int = 2
    short_utt_s: float = 1.0
    neighbor_gap_s: float = 1.5
    echo_containment: float = 0.8
    echo_min_tokens: int = 3
    echo_window_s: float = 1.0
    sessions_dir: Path = field(default_factory=lambda: Path("~/Desktop/Work/OnTrack/sessions").expanduser())
    app_support_dir: Path = field(
        default_factory=lambda: Path("~/Library/Application Support/OnTrack").expanduser()
    )
    notes_dir: Path = field(default_factory=lambda: Path("~/Desktop/Work/OnTrack/notes").expanduser())
    llm_model: str = "sonnet"
    claude_path: str = ""
    qa_timeout_s: float = 60
    summary_timeout_s: float = 300
    fallback_whisper_model: str = "mlx-community/whisper-small-mlx"
    degraded_lag_s: float = 20.0
    mic_hold_max_s: float = 15.0
    gap_marker_s: float = 10.0
    overlay_lines: int = 3
    retranscribe: bool = True


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


def load_config(path: Path | None) -> Config:
    if path is None or not Path(path).exists():
        return Config()
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    fields = {f.name: f for f in dataclasses.fields(Config)}
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(unknown)}")
    return Config(**{k: _coerce(fields[k], v) for k, v in data.items()})
