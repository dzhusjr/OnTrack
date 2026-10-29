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

## Product work to plan

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
