"""Model access check, one-time download, and offline mode.

Order matters: check Hugging Face access to the gated pyannote repos FIRST (seconds), and only then
download ~5 GB. After a verified download the engine runs with HF_HUB_OFFLINE=1 — no network, and the
token is no longer needed.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

# Speaker separation (pyannote). Free, but each needs "Agree and access repository" on its page.
GATED = (
    "pyannote/segmentation-3.0",
    "pyannote/speaker-diarization-3.1",
    "pyannote/speaker-diarization-community-1",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
)
MARKER = "models-ready.json"
TOKENS_URL = "https://huggingface.co/settings/tokens"


def whisper_repos(cfg: Config) -> list[str]:
    return list(dict.fromkeys([cfg.whisper_model, cfg.final_whisper_model, cfg.fallback_whisper_model]))


def all_repos(cfg: Config) -> list[str]:
    return whisper_repos(cfg) + list(GATED)


@dataclass
class AccessReport:
    user: str | None = None
    token_role: str | None = None
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check_access(hub=None) -> AccessReport:
    """Is there a usable token, and has this account accepted the terms of every gated repo?"""
    if hub is None:
        import huggingface_hub as hub
        import huggingface_hub.errors  # noqa: F401  (makes hub.errors available)
    errors = hub.errors

    r = AccessReport()
    token = os.environ.get("HF_TOKEN") or hub.get_token()
    if not token:
        r.problems.append(f"No Hugging Face token. Create a Read token at {TOKENS_URL}, then run: "
                          "cd engine && uv run hf auth login")
        return r
    try:
        info = hub.whoami(token=token)
    except errors.HfHubHTTPError as e:
        if _status(e) in (401, 403):
            r.problems.append(f"Your Hugging Face token was rejected (invalid or revoked). Make a new Read token at "
                              f"{TOKENS_URL} and run: cd engine && uv run hf auth login --force")
        else:
            r.problems.append(f"Could not reach Hugging Face ({e}). Check your internet connection and retry.")
        return r
    except Exception as e:  # network down, proxy, DNS…
        r.problems.append(f"Could not reach Hugging Face ({type(e).__name__}: {e}). Check your connection and retry.")
        return r
    r.user = info.get("name")
    r.token_role = ((info.get("auth") or {}).get("accessToken") or {}).get("role")
    if r.token_role == "fineGrained":
        r.warnings.append("Fine-grained token: it needs 'Read access to contents of all public gated repos you can "
                          "access'. A classic Read token is simpler.")
    for repo in GATED:
        try:
            hub.auth_check(repo, token=token)
        except errors.GatedRepoError:
            r.problems.append(f"Not accepted yet: open https://huggingface.co/{repo} (logged in as {r.user}), "
                              "fill in the short form and click 'Agree and access repository'.")
        except errors.RepositoryNotFoundError:
            r.problems.append(f"{repo}: not visible to this token — use a classic Read token ({TOKENS_URL}).")
        except Exception as e:
            r.problems.append(f"{repo}: access check failed ({type(e).__name__}: {e}).")
    return r


def _status(e: Exception) -> int | None:
    resp = getattr(e, "response", None)
    return getattr(resp, "status_code", None)


def download(cfg: Config, hub=None, log=print) -> None:
    """Fetch every model into the local cache (resumes if interrupted)."""
    if hub is None:
        import huggingface_hub as hub
    token = os.environ.get("HF_TOKEN") or hub.get_token()
    for repo in all_repos(cfg):
        log(f"  ↓ {repo}")
        hub.snapshot_download(repo, token=token if repo in GATED else None)


def verify_cached(cfg: Config, hub=None) -> list[str]:
    """Repos that are NOT fully available offline (empty list = all good)."""
    if hub is None:
        import huggingface_hub as hub
    missing = []
    for repo in all_repos(cfg):
        try:
            hub.snapshot_download(repo, local_files_only=True)
        except Exception:
            missing.append(repo)
    return missing


def write_marker(cfg: Config) -> Path:
    cfg.app_support_dir.mkdir(parents=True, exist_ok=True)
    p = cfg.app_support_dir / MARKER
    p.write_text(json.dumps({"repos": all_repos(cfg)}, indent=2), encoding="utf-8")
    return p


def offline_ready(cfg: Config) -> bool:
    """True when setup verified every model this config needs (changing a model in config.toml → online again)."""
    try:
        data = json.loads((cfg.app_support_dir / MARKER).read_text(encoding="utf-8"))
    except Exception:
        return False
    return set(all_repos(cfg)) <= set(data.get("repos", []))


def apply_offline_mode(cfg: Config) -> bool:
    """Engine start: no Hub calls once models are verified. ONTRACK_ONLINE=1 opts out."""
    if os.environ.get("ONTRACK_ONLINE") or not offline_ready(cfg):
        return False
    os.environ["HF_HUB_OFFLINE"] = "1"
    hub_constants = sys.modules.get("huggingface_hub.constants")
    if hub_constants is not None:  # already imported: flip the cached constant too
        hub_constants.HF_HUB_OFFLINE = True
    return True


def run_cli(cfg: Config, action: str, log=print) -> int:
    """`ontrack models check|download|verify` — used by scripts/dev.sh."""
    if action in ("check", "download"):
        log("Checking Hugging Face access…")
        rep = check_access()
        if rep.user:
            log(f"  logged in as {rep.user} (token: {rep.token_role or 'unknown'})")
        for w in rep.warnings:
            log(f"  ! {w}")
        for p in rep.problems:
            log(f"  ✗ {p}")
        if not rep.ok:
            log("Fix the items above, then run scripts/dev.sh setup again. Nothing was downloaded.")
            return 1
        log(f"  ✓ access to all {len(GATED)} speaker models")
        if action == "check":
            return 0
        log("Downloading models (one time, ~5 GB; safe to interrupt and rerun)…")
        download(cfg, log=log)
    missing = verify_cached(cfg)
    if missing:
        for m in missing:
            log(f"  ✗ not in the local cache: {m}")
        return 1
    write_marker(cfg)
    log("  ✓ all models cached — OnTrack now runs fully offline (no token needed from here on)")
    return 0
