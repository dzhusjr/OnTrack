"""Optional local language models (MLX or Ollama). Nothing here runs unless the user asks for it:
no import of mlx-lm, no model in memory, no network call until `LocalAi.start()` is requested."""
from __future__ import annotations

import gc
import importlib
import importlib.util
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import fnmatch
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from .config import Config
from .llm import LlmError

log = logging.getLogger(__name__)

MODES = ("claude", "local_first", "local_only")
PROVIDERS = ("mlx", "ollama")
TASKS = ("quick", "summary", "project")
_NOT_LLM = ("whisper", "pyannote", "wespeaker", "silero", "embedding", "segmentation")


def _hf_cache() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    home = Path(os.environ.get("HF_HOME", "~/.cache/huggingface")).expanduser()
    return home / "hub"


def installed_mlx_models(cache: Path | None = None) -> list[str]:
    """Language models already in the Hugging Face cache (never downloads anything)."""
    root = cache or _hf_cache()
    out: list[str] = []
    try:
        for d in sorted(root.glob("models--*--*")):
            _, org, name = d.name.split("--", 2)
            if any(w in name.lower() for w in _NOT_LLM):
                continue
            if any(d.glob("snapshots/*/config.json")) and any(d.glob("snapshots/*/*.safetensors")):
                out.append(f"{org}/{name}")
    except OSError:
        pass
    return out


def cached_snapshot(repo: str, cache: Path | None = None) -> str | None:
    """Folder of a model already in the Hugging Face cache, or None. Only the files a model needs have to be
    there (config + weights): `mlx_lm` itself skips README and .gitattributes, so a strict "complete snapshot"
    check would reject models it downloaded."""
    d = (cache or _hf_cache()) / ("models--" + repo.replace("/", "--"))
    candidates: list[Path] = []
    ref = d / "refs" / "main"
    try:
        if ref.exists():
            candidates.append(d / "snapshots" / ref.read_text().strip())
        candidates += sorted((d / "snapshots").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for snap in candidates:
        if (snap / "config.json").exists() and any(snap.glob("*.safetensors")):
            return str(snap)
    return None


# ---------------------------------------------------------------------------------------------- MLX
class MlxProvider:
    """mlx-lm in this process. All model work happens on one dedicated thread (MLX streams are per thread)."""
    name = "mlx"

    def __init__(self, lm=None, resolve: Callable[[str], str] | None = None, cache: Path | None = None) -> None:
        self._lm = lm  # injected in tests; otherwise imported on first load
        self._resolve = resolve
        self._cache = cache
        self._ex: ThreadPoolExecutor | None = None
        self._model = None
        self._tok = None
        self._loaded: str | None = None
        self._model_bytes = 0  # what loading this model added (the engine's Whisper models share MLX memory)

    # -- helpers
    def _module(self):
        if self._lm is None:
            try:
                import mlx_lm  # noqa: PLC0415 (lazy on purpose)
            except ImportError as e:
                raise LlmError("local_unavailable",
                               "Local AI is not installed. Press Install in Settings → Assistant → AI.") from e
            self._lm = mlx_lm
        return self._lm

    def _executor(self) -> ThreadPoolExecutor:
        if self._ex is None:
            self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ontrack-mlx")
        return self._ex

    def _local_path(self, repo: str) -> str:
        """The cached copy of a model. Never downloads: a missing model is an error with the fix."""
        if self._resolve is not None:
            return self._resolve(repo)
        if Path(repo).expanduser().is_dir():
            return str(Path(repo).expanduser())
        found = cached_snapshot(repo, self._cache)
        if found:
            return found
        raise LlmError("local_model_missing", f"{repo} is not downloaded. Press Install in Settings → Assistant → AI.")

    # -- provider interface
    def available(self) -> str | None:
        if self._lm is None and importlib.util.find_spec("mlx_lm") is None:  # looks only, imports nothing
            return "Local AI is not installed. Press Install in Settings → Assistant → AI."
        return None

    def installed(self) -> list[str]:
        return installed_mlx_models(self._cache)

    def loaded(self) -> str | None:
        return self._loaded

    def load(self, model: str) -> None:
        if self._loaded == model:
            return
        lm = self._module()
        path = self._local_path(model)

        def work():
            self._model = self._tok = None
            gc.collect()
            before = self._active_bytes()
            self._model, self._tok = lm.load(path)
            self._model_bytes = max(0, self._active_bytes() - before)
            self._loaded = model

        try:
            self._executor().submit(work).result()
        except LlmError:
            raise
        except Exception as e:  # noqa: BLE001
            self._loaded = None
            raise LlmError("local_failed", f"Could not load {model}: {e}") from e

    def unload(self) -> None:
        if self._ex is None:
            self._loaded = None
            return

        def work():
            self._model = self._tok = None
            self._loaded = None
            gc.collect()
            try:
                import mlx.core as mx  # noqa: PLC0415

                getattr(mx, "clear_cache", None) and mx.clear_cache()
            except Exception:  # noqa: BLE001
                pass

        self._ex.submit(work).result()
        self._ex.shutdown(wait=True)
        self._ex = None

    @staticmethod
    def _active_bytes() -> int:
        try:
            import mlx.core as mx  # noqa: PLC0415

            return int(mx.get_active_memory())
        except Exception:  # noqa: BLE001
            return 0

    def memory_mb(self) -> int | None:
        """Memory this model added when it was loaded (not the whole engine, which also holds Whisper)."""
        return int(self._model_bytes / 1e6) if self._loaded is not None and self._model_bytes else None

    def stream(self, prompt: str, model: str, max_tokens: int, timeout_s: float) -> Iterator[str]:
        if self._loaded != model:
            raise LlmError("local_not_loaded", f"{model} is not loaded.")
        lm = self._module()
        out: queue.Queue = queue.Queue()
        stop = threading.Event()

        def work():
            try:
                ids = self._tok.apply_chat_template([{"role": "user", "content": prompt}], add_generation_prompt=True)
                kwargs = {"max_tokens": max_tokens}
                try:
                    from mlx_lm.sample_utils import make_sampler  # noqa: PLC0415

                    kwargs["sampler"] = make_sampler(temp=0.3, top_p=0.9)
                except Exception:  # noqa: BLE001
                    pass
                for r in lm.stream_generate(self._model, self._tok, prompt=ids, **kwargs):
                    if stop.is_set():
                        break
                    if r.text:
                        out.put(r.text)
                out.put(None)
            except Exception as e:  # noqa: BLE001
                out.put(e)

        self._executor().submit(work)
        deadline = time.monotonic() + timeout_s
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LlmError("llm_timeout", f"The local model did not answer within {timeout_s:.0f} s")
                try:
                    item = out.get(timeout=remaining)
                except queue.Empty:
                    continue
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise LlmError("local_failed", f"Local model failed: {item}") from item
                yield item
        finally:
            stop.set()


# ------------------------------------------------------------------------------------------- Ollama
class OllamaProvider:
    """A running Ollama on this Mac (loopback only, so nothing leaves the machine)."""
    name = "ollama"

    def __init__(self, url: str = "http://127.0.0.1:11434", keep_alive: str = "30m") -> None:
        host = urlparse(url).hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise LlmError("local_unavailable", "The Ollama address must be on this Mac (127.0.0.1).")
        self.url = url.rstrip("/")
        self.keep_alive = keep_alive
        self._loaded: str | None = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy

    def _call(self, path: str, body: dict | None = None, timeout: float = 5.0):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        return self._opener.open(req, timeout=timeout)

    def available(self) -> str | None:
        try:
            self._call("/api/tags", timeout=2.0).close()
        except (urllib.error.URLError, OSError):
            return "Ollama is not running. Open the Ollama app or run: ollama serve"
        return None

    def installed(self) -> list[str]:
        try:
            with self._call("/api/tags", timeout=2.0) as r:
                return sorted(m["name"] for m in json.load(r).get("models", []))
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return []

    def loaded(self) -> str | None:
        return self._loaded

    def load(self, model: str) -> None:
        problem = self.available()
        if problem:
            raise LlmError("local_unavailable", problem)
        if model not in self.installed():
            raise LlmError("local_model_missing", f"Ollama does not have {model}. Run: ollama pull {model}")
        try:
            self._call("/api/generate", {"model": model, "prompt": "", "stream": False,
                                         "keep_alive": self.keep_alive}, timeout=120.0).close()
        except (urllib.error.URLError, OSError) as e:
            raise LlmError("local_failed", f"Ollama could not load {model}: {e}") from e
        self._loaded = model

    def unload(self) -> None:
        model, self._loaded = self._loaded, None
        if model:
            try:
                self._call("/api/generate", {"model": model, "prompt": "", "stream": False, "keep_alive": 0}).close()
            except (urllib.error.URLError, OSError):
                pass

    def memory_mb(self) -> int | None:
        return None  # Ollama manages its own memory

    def stream(self, prompt: str, model: str, max_tokens: int, timeout_s: float) -> Iterator[str]:
        if self._loaded != model:
            raise LlmError("local_not_loaded", f"{model} is not loaded.")
        body = {"model": model, "stream": True, "keep_alive": self.keep_alive,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"num_predict": max_tokens, "temperature": 0.3}}
        deadline = time.monotonic() + timeout_s
        try:
            with self._call("/api/chat", body, timeout=timeout_s) as r:
                for raw in r:
                    if time.monotonic() > deadline:
                        raise LlmError("llm_timeout", f"The local model did not answer within {timeout_s:.0f} s")
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    if ev.get("error"):
                        raise LlmError("local_failed", f"Ollama: {ev['error']}")
                    text = (ev.get("message") or {}).get("content")
                    if text:
                        yield text
                    if ev.get("done"):
                        return
        except (urllib.error.URLError, OSError) as e:
            raise LlmError("local_failed", f"Ollama stopped answering: {e}") from e



# ------------------------------------------------------------------------------------------- install
MODEL_FILES = ["*.json", "model*.safetensors", "*.py", "tokenizer.model", "*.tiktoken", "tiktoken.model", "*.txt",
               "*.jsonl", "*.jinja"]  # what mlx_lm itself fetches
_EXTRA_PATHS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")


def engine_dir() -> Path | None:
    d = Path(__file__).resolve().parents[2]
    return d if (d / "pyproject.toml").exists() else None


def install_package() -> None:
    """`uv sync` with the local-ai extra into this engine's environment (explicit request only)."""
    uv = shutil.which("uv", path=os.pathsep.join([os.environ.get("PATH", "")] + [os.path.expanduser(p) for p in _EXTRA_PATHS]))
    root = engine_dir()
    if uv is None or root is None:
        raise LlmError("local_setup_failed", "Could not find uv. In Terminal run: scripts/dev.sh local-ai")
    r = subprocess.run([uv, "sync", "--extra", "models", "--extra", "local-ai"], cwd=root, capture_output=True,
                       text=True, timeout=900)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["unknown error"]
        raise LlmError("local_setup_failed", f"Installing the local AI package failed: {tail[0]}")
    importlib.invalidate_caches()


def _remote_bytes(repo: str) -> int:
    try:
        with urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}?blobs=true", timeout=10) as r:
            files = json.load(r).get("siblings", [])
        return sum(f.get("size") or 0 for f in files if any(fnmatch.fnmatch(f["rfilename"], p) for p in MODEL_FILES))
    except (OSError, ValueError, KeyError):
        return 0


def _dir_bytes(d: Path) -> int:
    total = 0
    try:
        for f in d.rglob("*"):
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
    except OSError:
        pass
    return total


def download_model(repo: str, report: Callable[[float | None, str], None], cache: Path | None = None) -> None:
    """Download a public model with a plain `huggingface_hub` call in a separate process. The engine runs in
    offline mode, so the child gets a clean environment; this only ever happens when the user pressed Install."""
    total = _remote_bytes(repo)
    env = {k: v for k, v in os.environ.items() if k not in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
    code = "import sys; from huggingface_hub import snapshot_download as s; s(sys.argv[1], allow_patterns=%r)" % (MODEL_FILES,)
    proc = subprocess.Popen([sys.executable, "-c", code, repo], env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True)
    folder = (cache or _hf_cache()) / ("models--" + repo.replace("/", "--"))
    while proc.poll() is None:
        done = _dir_bytes(folder)
        report(min(done / total, 0.99) if total else None,
               f"{done / 1e9:.1f} of {total / 1e9:.1f} GB" if total else f"{done / 1e9:.1f} GB")
        time.sleep(1.0)
    if proc.returncode != 0:
        tail = (proc.stderr.read() or "").strip().splitlines()[-1:] or ["unknown error"]
        raise LlmError("local_setup_failed", f"Downloading {repo} failed: {tail[0]}")

# ----------------------------------------------------------------------------------------- manager
class LocalAi:
    """Which provider and models are chosen, and whether one is loaded right now.
    Loaded only by `start()` (an explicit request), freed by `stop()`; never at startup."""

    def __init__(self, cfg: Config, on_status: Callable[[], None] | None = None,
                 providers: dict[str, Callable[[], object]] | None = None,
                 install_package_fn: Callable[[], None] | None = None,
                 download_fn: Callable[[str, Callable[[float | None, str], None]], None] | None = None,
                 online_fn: Callable[[], bool] | None = None) -> None:
        self.cfg = cfg
        self._install_package = install_package_fn or install_package
        self._download = download_fn or download_model
        self._online = online_fn
        self.progress: float | None = None
        self.needs_setup = ""  # "" | "package" | "model": what Install would do (mlx only)
        self.on_status = on_status or (lambda: None)
        self._factories = providers or {
            "mlx": MlxProvider,
            "ollama": lambda: OllamaProvider(self.cfg.ollama_url, self.cfg.ollama_keep_alive),
        }
        self._providers: dict[str, object] = {}
        self._lock = threading.RLock()
        self.mode = cfg.ai_mode if cfg.ai_mode in MODES else "claude"
        self.provider = cfg.local_provider if cfg.local_provider in PROVIDERS else "mlx"
        self.models = {"quick": cfg.local_model_quick, "summary": cfg.local_model_summary,
                       "project": cfg.local_model_project}
        self.state = "off"  # off | loading | ready | error
        self.message = ""
        self.last = ""  # what answered the most recent request
        self._installed: list[str] = []

    # -- settings (never load anything)
    def configure(self, mode: str | None = None, provider: str | None = None, models: dict | None = None) -> None:
        with self._lock:
            if provider in PROVIDERS and provider != self.provider:
                self._unload_locked()
                self.provider = provider
            if mode in MODES:
                self.mode = mode
                if mode == "claude":
                    self._unload_locked()  # turning local AI off frees the memory right away
            for task, model in (models or {}).items():
                if task in TASKS and isinstance(model, str):
                    self.models[task] = model.strip()
        self.on_status()

    def _prov(self):
        if self.provider not in self._providers:
            self._providers[self.provider] = self._factories[self.provider]()
        return self._providers[self.provider]

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    # -- explicit one-time install (package + model); never loads the model
    def install(self) -> None:
        with self._lock:
            if self.state in ("loading", "installing"):
                return
            if self.provider != "mlx":
                self.state, self.message = "error", "Ollama keeps its own models. In Terminal: ollama pull <model>"
                self.on_status()
                return
            if self.state == "ready":
                self._unload_locked()  # the package may be replaced
            self.state, self.message, self.progress = "installing", "Preparing…", None
        self.on_status()
        threading.Thread(target=self._install, daemon=True, name="ontrack-local-install").start()

    def _report(self, progress: float | None, message: str) -> None:
        with self._lock:
            self.progress, self.message = progress, message
        self.on_status()

    def _install(self) -> None:
        try:
            online = self._online
            if online is None:
                from .ai import dns_online as online  # noqa: PLC0415
            if not online():
                raise LlmError("local_setup_failed", "No internet. Installing needs a connection once.")
            prov = self._prov()
            if prov.available():
                self._report(None, "Installing the local AI package…")
                self._install_package()
            model = self.models["quick"]
            if model and not cached_snapshot(model, getattr(prov, "_cache", None)):
                self._report(None, f"Downloading {model.rsplit('/', 1)[-1]}…")
                self._download(model, self._report)
            with self._lock:
                self.state, self.message, self.progress = "off", "Installed. Press Load when you want to use it.", None
        except LlmError as e:
            with self._lock:
                self.state, self.message, self.progress = "error", e.message, None
            log.warning("local AI install failed: %s", e.message)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self.state, self.message, self.progress = "error", f"Install failed: {e}", None
            log.exception("local AI install failed")
        self._update_needs_setup()
        self.on_status()

    def _update_needs_setup(self) -> None:
        if self.provider != "mlx":
            self.needs_setup = ""
            return
        prov = self._prov()
        if prov.available():
            self.needs_setup = "package"
        else:
            model = self.models["quick"]
            self.needs_setup = "model" if model and not cached_snapshot(model, getattr(prov, "_cache", None)) else ""

    # -- explicit load / unload
    def start(self) -> None:
        """Load the quick model in the background. Status messages report progress and errors."""
        with self._lock:
            if self.mode == "claude":
                self.state, self.message = "error", "Local AI is off. Choose Local first or Local only in Settings."
                self.on_status()
                return
            if self.state == "loading":
                return
            model = self.models["quick"]
            if not model:
                self.state, self.message = "error", "Choose a quick-reply model first."
                self.on_status()
                return
            self.state, self.message = "loading", f"Loading {model}…"
        self.on_status()
        threading.Thread(target=self._load, args=(model,), daemon=True, name="ontrack-local-load").start()

    def _load(self, model: str) -> None:
        try:
            self._prov().load(model)
            with self._lock:
                self.state, self.message = "ready", ""
        except LlmError as e:
            with self._lock:
                self.state, self.message = "error", e.message
            log.warning("local model load failed: %s", e.message)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self.state, self.message = "error", f"Could not load {model}: {e}"
            log.exception("local model load failed")
        self.on_status()

    def stop(self) -> None:
        with self._lock:
            self._unload_locked()
        self.on_status()

    def _unload_locked(self) -> None:
        prov = self._providers.get(self.provider)
        if prov is not None:
            try:
                prov.unload()
            except Exception:  # noqa: BLE001
                log.exception("unloading the local model failed")
        self.state, self.message = "off", ""

    def refresh(self) -> None:
        try:
            prov = self._prov()
            self._installed = prov.installed()
            self._update_needs_setup()
            if self.state == "off":
                self.message = prov.available() or ""
        except LlmError as e:
            self._installed, self.message = [], e.message
        self.on_status()

    # -- use
    def stream(self, prompt: str, model: str, max_tokens: int, timeout_s: float) -> Iterator[str]:
        """Generate with `model` (loaded now if another model is resident: only while local AI is on)."""
        with self._lock:
            if self.state != "ready":
                raise LlmError("local_not_loaded", "The local model is not loaded. Press Load in Settings → Assistant.")
            prov = self._prov()
            if prov.loaded() != model:
                self.state, self.message = "loading", f"Loading {model}…"
                self.on_status()
                try:
                    prov.load(model)
                except LlmError as e:
                    self.state, self.message = "error", e.message
                    self.on_status()
                    raise
                self.state, self.message = "ready", ""
                self.on_status()
        yield from prov.stream(prompt, model, max_tokens, timeout_s)

    def status(self) -> dict:
        prov = self._providers.get(self.provider)
        return {
            "type": "ai_status", "state": self.state, "mode": self.mode, "provider": self.provider,
            "model": (prov.loaded() if prov is not None else None) or "",
            "message": self.message, "installed": list(self._installed),
            "models": dict(self.models), "last": self.last,
            "memory_mb": prov.memory_mb() if prov is not None and self.state == "ready" else None,
            "needs_setup": self.needs_setup, "progress": self.progress,
        }


# -------------------------------------------------------------------------------- manual check (Mac)
def main(argv: list[str] | None = None) -> int:
    """`uv run python -m ontrack.localai [model]`: load the model, answer one prompt, report speed and memory, free it.
    Run by `scripts/dev.sh local-ai`. This is the only place that loads a model without the app asking."""
    import argparse

    cfg = Config()
    ap = argparse.ArgumentParser(prog="ontrack.localai")
    ap.add_argument("model", nargs="?", default=cfg.local_model_quick)
    ap.add_argument("--provider", choices=PROVIDERS, default="mlx")
    args = ap.parse_args(argv)
    prov = MlxProvider() if args.provider == "mlx" else OllamaProvider(cfg.ollama_url)
    try:
        problem = prov.available()
        if problem:
            print("✗", problem)
            return 1
        t0 = time.monotonic()
        prov.load(args.model)
        print(f"✓ loaded {args.model} in {time.monotonic() - t0:.1f} s")
        t1, first, n = time.monotonic(), None, 0
        out = []
        for chunk in prov.stream("Answer in one short sentence: what is a mutex?", args.model, 80, 120):
            first = first if first is not None else time.monotonic() - t1
            n += 1
            out.append(chunk)
        total = time.monotonic() - t1
        print(f"✓ first word after {first or 0:.2f} s, {n / max(total, 1e-6):.0f} chunks/s")
        print("  " + "".join(out).strip())
        mem = prov.memory_mb()
        if mem is not None:
            print(f"  memory in use: {mem / 1000:.1f} GB")
        return 0
    except LlmError as e:
        print("✗", e.message)
        return 1
    finally:
        prov.unload()
        print("✓ unloaded")


if __name__ == "__main__":
    import sys

    sys.exit(main())
