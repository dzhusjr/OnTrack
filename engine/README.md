# OnTrack engine

Python engine for OnTrack: VAD segmentation, local Whisper transcription, speaker labelling, crash-safe session files.

```bash
cd engine
uv sync                      # core + dev (unit tests run on any OS)
uv run pytest -m "not models"
uv sync --extra models       # Mac only: mlx-whisper, silero-vad, pyannote
```

## Mac setup for real models (Task 10 / M1 acceptance)

1. Hugging Face (free): follow step 2 of the main README (accept the 4 pyannote repos, Read token), then
   `scripts/dev.sh setup` — it checks access first and only then downloads and verifies every model
   (`uv run ontrack models check|download|verify` does the same by hand).
2. Install voices Samantha, Daniel, Milena, Lesya (System Settings → Accessibility → Spoken Content → Manage Voices).
3. Run:
   ```bash
   cd engine
   uv sync --extra models
   uv run python scripts/make_fixtures.py
   uv run pytest -m models -v
   ```
4. Try it on a real recording:
   ```bash
   afconvert -f WAVE -d LEI16@16000 -c 1 recording.m4a rec.wav
   uv run ontrack enroll --wav me_30s.wav      # ~30 s of you talking
   uv run ontrack transcribe rec.wav --name "Smoke test"
   ```

Speaker threshold `tau` (default 0.55) can be tuned in `~/Library/Application Support/OnTrack/config.toml`.
