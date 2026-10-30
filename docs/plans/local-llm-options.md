# Local LLM options

This note records the local-model direction discussed for OnTrack and future
coding/creative-assistant integrations. It is a plan, not an enabled default.

## Recommended hardware split

- **16 GB Apple Silicon Mac:** keep the current MLX Qwen3 4B path for fast,
  private replies. A 7–9B 4-bit model can be offered as a slower quality mode;
  avoid loading 24–30B models alongside the app.
- **32 GB Windows laptop with RTX 4070:** use Ollama for heavier local work.
  Test Qwen3-Coder 30B first, then Devstral Small 2 if memory spill makes it
  too slow. The model server should be activated only when requested.

## Implemented: optional local provider (first slice)

Claude stays the default. The engine routes each request through `ai.AiRouter`:

- **Modes** (`ai_mode`, Settings → Assistant → AI): `claude` (default), `local_first`, `local_only`.
  `local_first` answers locally once the model is loaded and falls back to Claude if it is not loaded or fails
  before answering (that request is then sent to Claude). `local_only` never calls Claude: it returns a clear error
  instead. Connectors need Claude, so they are used only in `local_first`; project questions get no file tools locally.
- **Lifecycle:** choosing a mode loads nothing. `local_ai start` (the **Load** button) loads the quick model,
  `local_ai stop` (**Unload**) or switching back to Claude frees it. Models for summaries and project questions load
  on first use while local AI is on and replace the resident one, so only one model is in memory.
- **Models per task:** `local_model_quick` (default `mlx-community/Qwen3-4B-Instruct-2507-4bit`),
  `local_model_summary`, `local_model_project`. Empty means Claude in `local_first`, the quick model in `local_only`.
- **Providers:** MLX in-process (`mlx-lm`, loads only from the local Hugging Face cache, never downloads) and
  Ollama over loopback only (`ollama_url`, native API, `keep_alive` honoured, unloaded on Unload).
- **Status:** the engine sends `ai_status` (state, loaded model, memory for MLX, installed models, last route with
  first-word latency). Settings shows it; the menu shows a line when a local mode is on.
- **Not done yet:** an OpenAI-compatible endpoint, a Windows/Ollama profile for 30B coding models, idle auto-unload,
  and benchmarks (first-token latency, tokens/s, peak RAM) on the fixed prompt set below.
- **Check on a Mac:** `scripts/dev.sh local-ai` loads the model once, answers a prompt, prints load time, latency
  and memory, then unloads.

## Product work still to plan

- Add an explicit local-provider setting (off by default) with a clear
  **Start local model** / **Stop local model** lifecycle so idle sessions do
  not reserve model memory.
- Support an OpenAI-compatible local endpoint (Ollama/MLX server) behind the
  same provider interface used for Claude. Keep model selection separate for
  quick replies, summaries, and project/code tasks.
- Add a provider health check showing model name, loaded state, memory estimate,
  and latency before routing a request locally.
- Keep Claude as the fallback for complex or high-risk tasks; local models are
  a privacy/offline option, not an Opus-equivalent guarantee.
- For free-form fictional text, allow an explicitly selected permissive local
  model such as Dolphin Mistral, but do not make it the default or expose the
  local endpoint to the public internet.

## First experiments

```text
Mac:     existing MLX Qwen3 4B (fast replies)
Windows: qwen3-coder:30b, then devstral-small-2 (agentic coding)
Creative: dolphin-mistral (permissive text; older model)
```

Before implementation, measure first-token latency, tokens/second, peak RAM,
and quality on a fixed set of OnTrack reply, summary, and project-edit prompts.
