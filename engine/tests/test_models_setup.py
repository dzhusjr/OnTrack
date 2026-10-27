"""Setup checks HF access before downloading anything; verified models switch the engine offline."""
import os
import sys
import types
from dataclasses import replace

import pytest

from ontrack import models_setup as ms
from ontrack.config import load_config


class HTTPError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = types.SimpleNamespace(status_code=status)


class GatedRepoError(HTTPError):
    pass


class RepositoryNotFoundError(HTTPError):
    pass


def fake_hub(token="hf_x", role="read", gated=(), whoami_status=None, cached=None):
    calls = []
    cached = set(cached if cached is not None else [])

    def whoami(token):
        calls.append(("whoami",))
        if whoami_status:
            raise HTTPError(whoami_status)
        return {"name": "alex", "auth": {"accessToken": {"role": role}}}

    def auth_check(repo, token):
        calls.append(("auth_check", repo))
        if repo in gated:
            raise GatedRepoError(403)

    def snapshot_download(repo, token=None, local_files_only=False):
        calls.append(("local" if local_files_only else "download", repo))
        if local_files_only and repo not in cached:
            raise FileNotFoundError(repo)
        cached.add(repo)
        return "/cache/" + repo

    errors = types.SimpleNamespace(HfHubHTTPError=HTTPError, GatedRepoError=GatedRepoError,
                                   RepositoryNotFoundError=RepositoryNotFoundError)
    hub = types.SimpleNamespace(get_token=lambda: token, whoami=whoami, auth_check=auth_check,
                                snapshot_download=snapshot_download, errors=errors)
    return hub, calls


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("ONTRACK_ONLINE", raising=False)
    return replace(load_config(None), app_support_dir=tmp_path)


def test_missing_token_is_reported_without_network():
    hub, calls = fake_hub(token=None)
    r = ms.check_access(hub)
    assert not r.ok and "No Hugging Face token" in r.problems[0] and calls == []


def test_unaccepted_repos_are_listed_with_links():
    hub, _ = fake_hub(gated={"pyannote/speaker-diarization-community-1"})
    r = ms.check_access(hub)
    assert len(r.problems) == 1
    assert "https://huggingface.co/pyannote/speaker-diarization-community-1" in r.problems[0]


def test_rejected_token_and_fine_grained_warning():
    hub, _ = fake_hub(whoami_status=401)
    assert "rejected" in ms.check_access(hub).problems[0]
    hub, _ = fake_hub(role="fineGrained")
    r = ms.check_access(hub)
    assert r.ok and "gated repos" in r.warnings[0]


def test_run_cli_checks_access_before_downloading(cfg, monkeypatch):
    hub, calls = fake_hub(gated={"pyannote/segmentation-3.0"})
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", hub.errors)
    out = []
    assert ms.run_cli(cfg, "download", log=out.append) == 1
    assert not any(c[0] == "download" for c in calls)  # nothing downloaded when access is missing
    assert "Nothing was downloaded" in out[-1] and not ms.offline_ready(cfg)


def test_download_then_offline_marker(cfg, monkeypatch):
    hub, calls = fake_hub()
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", hub.errors)
    assert ms.run_cli(cfg, "download", log=lambda *_: None) == 0
    first_download = next(i for i, c in enumerate(calls) if c[0] == "download")
    assert all(c[0] in ("whoami", "auth_check") for c in calls[:first_download])
    assert {c[1] for c in calls if c[0] == "download"} == set(ms.all_repos(cfg))
    assert ms.offline_ready(cfg)


def test_offline_mode_only_when_verified_and_not_opted_out(cfg, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert not ms.apply_offline_mode(cfg)
    ms.write_marker(cfg)
    monkeypatch.setenv("ONTRACK_ONLINE", "1")
    assert not ms.apply_offline_mode(cfg)
    monkeypatch.delenv("ONTRACK_ONLINE")
    assert ms.apply_offline_mode(cfg) and os.environ["HF_HUB_OFFLINE"] == "1"


def test_changing_a_model_in_config_needs_setup_again(cfg):
    ms.write_marker(cfg)
    other = replace(cfg, final_whisper_model="mlx-community/whisper-medium-mlx")
    assert not ms.offline_ready(other)
