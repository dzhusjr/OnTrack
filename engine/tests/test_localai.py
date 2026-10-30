"""Optional local AI: Claude stays the default, nothing loads unless asked, local_only never reaches Claude."""
import asyncio
import json
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fakes import frames
from test_engine import SPEECH, Client, dirs, make_service, run_with, typ  # noqa: F401

from ontrack.ai import AiRouter, for_task
from ontrack.config import Config
from ontrack.llm import LlmError
from ontrack.localai import LocalAi, MlxProvider, OllamaProvider, installed_mlx_models
from ontrack.types import Channel

QUICK = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
BIG = "mlx-community/Qwen3-8B-4bit"


class FakeClaude:
    def __init__(self, chunks=("claude says hi",)):
        self.chunks, self.calls = list(chunks), []

    def stream(self, prompt, timeout_s, **kw):
        self.calls.append((prompt, kw))
        yield from self.chunks

    def complete(self, prompt, timeout_s):
        return "".join(self.stream(prompt, timeout_s))


class ForbiddenClaude:
    """local_only must never get here."""

    def stream(self, prompt, timeout_s, **kw):
        raise AssertionError("Claude was called in local_only mode")
        yield  # pragma: no cover

    complete = stream


class FakeProvider:
    name = "mlx"

    def __init__(self, chunks=("local ", "reply"), fail=None, fail_after=None):
        self.chunks, self.fail, self.fail_after = list(chunks), fail, fail_after
        self.loads, self.unloads, self.prompts = [], 0, []
        self._loaded = None

    def available(self): return None
    def installed(self): return [QUICK, BIG]
    def loaded(self): return self._loaded
    def memory_mb(self): return 2400

    def load(self, model):
        self.loads.append(model)
        self._loaded = model

    def unload(self):
        self.unloads += 1
        self._loaded = None

    def stream(self, prompt, model, max_tokens, timeout_s):
        self.prompts.append((prompt, model, max_tokens))
        if self.fail and self.fail_after is None:
            raise LlmError("local_failed", self.fail)
        for i, c in enumerate(self.chunks):
            if self.fail and i == self.fail_after:
                raise LlmError("local_failed", self.fail)
            yield c


def make(mode="claude", claude=None, provider=None, **cfg_kw):
    cfg = replace(Config(), ai_mode=mode, **cfg_kw)
    prov = provider or FakeProvider()
    created = []
    local = LocalAi(cfg, providers={"mlx": lambda: created.append(1) or prov})
    return AiRouter(claude or FakeClaude(), cfg, local), local, prov, created


def wait_for(local, state, timeout=3.0):
    end = time.monotonic() + timeout
    while local.state != state and time.monotonic() < end:
        time.sleep(0.01)
    assert local.state == state, (local.state, local.message)


def ready(router, local):
    local.start()
    wait_for(local, "ready")


# ---- default behaviour: Claude, nothing loaded ---------------------------------------------------
def test_default_mode_is_claude_and_never_creates_or_loads_a_provider():
    router, local, prov, created = make("claude")
    assert Config().ai_mode == "claude"
    out = "".join(for_task(router, "quick").stream("hi", 5))
    assert out == "claude says hi" and created == [] and prov.loads == []
    assert local.status()["state"] == "off"


def test_choosing_a_local_mode_does_not_load_anything():
    router, local, prov, created = make("claude")
    local.configure(mode="local_first", provider="mlx", models={"quick": QUICK})
    assert local.mode == "local_first" and local.state == "off"
    assert created == [] and prov.loads == []
    # not loaded yet → local_first still answers with Claude
    assert "".join(for_task(router, "quick").stream("hi", 5)) == "claude says hi"
    assert router.local.last.startswith("Claude (local model not loaded")


def test_start_is_the_only_thing_that_loads_and_stop_frees():
    router, local, prov, created = make("local_first")
    local.start()
    wait_for(local, "ready")
    assert prov.loads == [QUICK] and local.status()["model"] == QUICK and local.status()["memory_mb"] == 2400
    local.stop()
    assert local.state == "off" and prov.unloads == 1 and local.status()["model"] == ""


def test_start_is_refused_while_local_ai_is_off():
    router, local, prov, created = make("claude")
    local.start()
    assert local.state == "error" and "off" in local.message.lower() and prov.loads == []


def test_turning_local_ai_off_unloads_the_model():
    router, local, prov, _ = make("local_first")
    ready(router, local)
    local.configure(mode="claude")
    assert local.state == "off" and prov.unloads == 1


def test_start_without_a_model_explains():
    router, local, prov, _ = make("local_first", local_model_quick="")
    local.start()
    assert local.state == "error" and "model" in local.message


# ---- routing -----------------------------------------------------------------------------------
def test_quick_replies_go_local_when_loaded_and_summaries_stay_on_claude_in_local_first():
    claude = FakeClaude()
    router, local, prov, _ = make("local_first", claude)
    ready(router, local)
    assert "".join(for_task(router, "quick").stream("q", 5, model="haiku")) == "local reply"
    assert prov.prompts[0][1:] == (QUICK, 400) and claude.calls == []
    assert local.last.startswith("Local · Qwen3-4B-Instruct-2507-4bit, ") and "s to first word" in local.last
    assert for_task(router, "summary").complete("s", 5) == "claude says hi"  # no local summary model chosen
    assert claude.calls and local.last.startswith("Claude")


def test_separate_models_per_task_load_on_demand_only_while_local_is_on():
    claude = FakeClaude()
    router, local, prov, _ = make("local_first", claude, local_model_summary=BIG)
    ready(router, local)
    assert prov.loads == [QUICK]  # only the quick model at start
    assert for_task(router, "summary").complete("s", 5) == "local reply"
    assert prov.loads == [QUICK, BIG] and prov.prompts[-1][2] == 1800 and claude.calls == []


def test_connectors_need_claude_in_local_first():
    claude = FakeClaude()
    router, local, prov, _ = make("local_first", claude)
    ready(router, local)
    "".join(for_task(router, "quick").stream("q", 5, connectors=True))
    assert claude.calls and claude.calls[0][1] == {"connectors": True} and prov.prompts == []


def test_project_tasks_use_the_project_model_and_tell_it_it_has_no_tools(tmp_path):
    router, local, prov, _ = make("local_first", local_model_project=QUICK)
    ready(router, local)
    "".join(for_task(router, "project").stream("what changed?", 5, project_dir=tmp_path))
    assert prov.prompts[0][0].startswith("You cannot open files") and prov.prompts[0][0].endswith("what changed?")


def test_local_first_falls_back_to_claude_when_local_fails_before_answering():
    claude = FakeClaude()
    router, local, prov, _ = make("local_first", claude, FakeProvider(fail="boom"))
    ready(router, local)
    assert "".join(for_task(router, "quick").stream("q", 5)) == "claude says hi"
    assert local.last == "Claude (fallback: local model failed)"


def test_a_half_finished_local_answer_is_not_redone_on_claude():
    claude = FakeClaude()
    router, local, prov, _ = make("local_first", claude, FakeProvider(fail="boom", fail_after=1))
    ready(router, local)
    got = []
    with pytest.raises(LlmError):
        for c in for_task(router, "quick").stream("q", 5):
            got.append(c)
    assert got == ["local "] and claude.calls == []


# ---- privacy: local_only never reaches Claude -----------------------------------------------------
def test_local_only_never_calls_claude_when_the_model_is_not_loaded():
    router, local, prov, _ = make("local_only", ForbiddenClaude())
    with pytest.raises(LlmError) as e:
        "".join(for_task(router, "quick").stream("secret transcript", 5))
    assert e.value.code == "local_not_loaded" and "Load" in e.value.message


def test_local_only_never_calls_claude_when_local_fails():
    router, local, prov, _ = make("local_only", ForbiddenClaude(), FakeProvider(fail="boom"))
    ready(router, local)
    with pytest.raises(LlmError) as e:
        "".join(for_task(router, "quick").stream("secret", 5))
    assert e.value.code == "local_failed"


def test_local_only_uses_the_quick_model_for_every_task_and_drops_connectors():
    router, local, prov, _ = make("local_only", ForbiddenClaude())
    ready(router, local)
    assert for_task(router, "summary").complete("notes please", 5) == "local reply"
    "".join(for_task(router, "quick").stream("q", 5, connectors=True, model="haiku"))
    assert [p[1] for p in prov.prompts] == [QUICK, QUICK]


def test_local_only_without_any_model_is_an_error_not_a_claude_call():
    router, local, prov, _ = make("local_only", ForbiddenClaude(), local_model_quick="")
    with pytest.raises(LlmError) as e:
        router.route("quick", False)
    assert e.value.code == "local_model_missing"


# ---- providers --------------------------------------------------------------------------------------
def test_mlx_provider_loads_lazily_from_the_cache_and_streams(tmp_path):
    class Resp:
        def __init__(self, text): self.text = text

    class Tok:
        def apply_chat_template(self, messages, add_generation_prompt):
            assert messages[0]["role"] == "user" and add_generation_prompt
            return [1, 2, 3]

    class Lm:
        loaded = []

        def load(self, path):
            self.loaded.append(path)
            return "model", Tok()

        def stream_generate(self, model, tok, prompt, max_tokens, **kw):
            assert prompt == [1, 2, 3] and max_tokens == 50
            for t in ("Hel", "lo"):
                yield Resp(t)

    lm = Lm()
    prov = MlxProvider(lm=lm, resolve=lambda repo: f"/cache/{repo}")
    assert lm.loaded == []  # constructing loads nothing
    prov.load(QUICK)
    assert lm.loaded == [f"/cache/{QUICK}"] and prov.loaded() == QUICK
    assert "".join(prov.stream("hi", QUICK, 50, 5)) == "Hello"
    prov.unload()
    assert prov.loaded() is None


def test_mlx_missing_model_error_says_how_to_fix_it_and_never_downloads(monkeypatch):
    prov = MlxProvider(lm=object())
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with pytest.raises(LlmError) as e:
        prov.load("mlx-community/does-not-exist-4bit")
    assert e.value.code == "local_model_missing" and "hf download" in e.value.message


def test_mlx_not_installed_is_reported_without_importing(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_lm", None)  # import would fail
    assert "mlx_lm" not in {m for m in sys.modules if sys.modules[m] is not None}
    prov = MlxProvider()
    assert prov.available() is None or "not installed" in prov.available()


def test_installed_models_skips_speech_models(tmp_path):
    def repo(name, safetensors=True):
        snap = tmp_path / f"models--{name.replace('/', '--')}" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}")
        if safetensors:
            (snap / "model.safetensors").write_text("x")

    repo("mlx-community/Qwen3-4B-Instruct-2507-4bit")
    repo("mlx-community/whisper-large-v3-turbo")
    repo("pyannote/segmentation-3.0")
    repo("mlx-community/half-downloaded", safetensors=False)
    assert installed_mlx_models(tmp_path) == ["mlx-community/Qwen3-4B-Instruct-2507-4bit"]


class _OllamaHandler(BaseHTTPRequestHandler):
    log: list = []

    def log_message(self, *a): pass

    def do_GET(self):
        body = json.dumps({"models": [{"name": "qwen3:4b"}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _OllamaHandler.log.append((self.path, req))
        self.send_response(200); self.end_headers()
        if self.path == "/api/chat":
            for t, done in (("Hi ", False), ("there", False), ("", True)):
                self.wfile.write((json.dumps({"message": {"content": t}, "done": done}) + "\n").encode())
        else:
            self.wfile.write(b"{}")


def test_ollama_provider_talks_to_a_local_server_and_unloads():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _OllamaHandler.log.clear()
        prov = OllamaProvider(f"http://127.0.0.1:{srv.server_port}", "10m")
        assert prov.available() is None and prov.installed() == ["qwen3:4b"]
        with pytest.raises(LlmError) as e:
            prov.load("nope:1b")
        assert e.value.code == "local_model_missing" and "ollama pull nope:1b" in e.value.message
        prov.load("qwen3:4b")
        assert "".join(prov.stream("hello", "qwen3:4b", 100, 5)) == "Hi there"
        chat = [r for p, r in _OllamaHandler.log if p == "/api/chat"][0]
        assert chat["options"]["num_predict"] == 100 and chat["keep_alive"] == "10m"
        prov.unload()
        assert _OllamaHandler.log[-1][1]["keep_alive"] == 0 and prov.loaded() is None
    finally:
        srv.shutdown()


def test_ollama_must_be_on_this_mac_and_reports_when_not_running():
    with pytest.raises(LlmError):
        OllamaProvider("http://example.com:11434")
    assert "not running" in OllamaProvider("http://127.0.0.1:1").available()


# ---- engine wiring ------------------------------------------------------------------------------------
def test_engine_announces_status_loads_nothing_and_local_only_blocks_claude(dirs):
    svc, cfg = make_service(dirs, [({"en": 1}, None)], runner_script=[["should never be used"]])
    created = []
    svc.ai._factories["mlx"] = lambda: created.append(1) or FakeProvider()
    svc.cfg = replace(cfg, suggest_delay_s=0.0)

    async def body():
        c = await Client(cfg).connect()
        st = await c.expect(typ("ai_status"))
        assert (st["state"], st["mode"]) == ("off", "claude") and created == []
        await c.send({"type": "set_ai", "mode": "local_only", "provider": "mlx", "quick": QUICK})
        st = await c.expect(lambda m: m["type"] == "ai_status" and m["mode"] == "local_only")
        assert st["state"] == "off" and created == []  # chosen, still nothing loaded
        await c.send({"type": "start_session", "name": "S"})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.send({"type": "suggest", "id": "s1"})
        err = await c.expect(lambda m: m["type"] == "error" and m["code"] != "no_voiceprint")
        assert err["code"] == "local_not_loaded"
        await c.expect(typ("suggestion_done"))
        await c.send({"type": "local_ai", "action": "start"})
        await c.expect(lambda m: m["type"] == "ai_status" and m["state"] == "ready")
        await c.send({"type": "local_ai", "action": "stop"})
        await c.expect(lambda m: m["type"] == "ai_status" and m["state"] == "off")
        await c.close()

    asyncio.run(run_with(svc, body))
    assert svc.runner.prompts == []  # Claude was never asked
