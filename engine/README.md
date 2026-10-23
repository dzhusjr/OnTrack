# OnTrack engine

Python engine for OnTrack: VAD segmentation, local Whisper transcription, speaker labelling, crash-safe session files.

```bash
cd engine
uv sync                      # core + dev (unit tests run on any OS)
uv run pytest -m "not models"
uv sync --extra models       # Mac only: mlx-whisper, silero-vad, pyannote
```
