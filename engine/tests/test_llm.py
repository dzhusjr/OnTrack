import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ontrack.config import load_config
from ontrack.llm import ClaudeRunner, LlmError, find_claude

FAKE = Path(__file__).parent / "fake_claude.py"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    exe.chmod(0o755)
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "log.json"))
    return replace(load_config(None), claude_path=str(exe))


def test_streams_partial_deltas(fake, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "deltas")
    assert list(ClaudeRunner(fake).stream("hi", 10)) == ["Hel", "lo ", "Alex"]


def test_assistant_fallback_without_deltas(fake, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "assistant")
    assert ClaudeRunner(fake).complete("hi", 10) == "Whole answer"


def test_prompt_sent_on_stdin_with_headless_flags(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "deltas")
    ClaudeRunner(fake).complete("the prompt", 10)
    log = json.loads((tmp_path / "log.json").read_text())
    assert log["stdin"] == "the prompt"
    assert log["argv"][:5] == ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    assert log["argv"][5:7] == ["--model", "sonnet"]
    assert Path(log["cwd"]).resolve() != Path.cwd().resolve()


def test_missing_binary_error_code(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("ontrack.llm._EXTRA_PATHS", ())
    cfg = replace(load_config(None), claude_path="")
    assert find_claude(cfg) is None
    with pytest.raises(LlmError) as e:
        list(ClaudeRunner(cfg).stream("x", 5))
    assert e.value.code == "llm_missing"


def test_timeout_kills_process(fake, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "sleep")
    with pytest.raises(LlmError) as e:
        list(ClaudeRunner(fake).stream("x", 1))
    assert e.value.code == "llm_timeout"


def test_rate_limit_mapped(fake, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ratelimit")
    with pytest.raises(LlmError) as e:
        ClaudeRunner(fake).complete("x", 10)
    assert e.value.code == "llm_rate_limit"


def test_auth_failure_mapped(fake, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "auth")
    with pytest.raises(LlmError) as e:
        ClaudeRunner(fake).complete("x", 10)
    assert e.value.code == "llm_auth"


def test_old_cli_without_flags_falls_back(fake, monkeypatch, tmp_path):  # review M2 #13
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "deltas")
    monkeypatch.setenv("FAKE_CLAUDE_REJECT", "--strict-mcp-config")
    cfg = replace(fake, llm_extra_args=("--strict-mcp-config", "--tools", ""))
    assert ClaudeRunner(cfg).complete("hi", 10) == "Hello Alex"


def test_default_flags_restrict_tools_and_mcp(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "deltas")
    ClaudeRunner(replace(fake, llm_extra_args=load_config(None).llm_extra_args)).complete("x", 10)
    argv = json.loads((tmp_path / "log.json").read_text())["argv"]
    assert "--strict-mcp-config" in argv and argv[argv.index("--tools") + 1] == ""


def test_node_missing_maps_to_llm_missing(fake, monkeypatch):  # review M2 #12
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "nonode")
    with pytest.raises(LlmError) as e:
        ClaudeRunner(fake).complete("x", 10)
    assert e.value.code == "llm_missing"
