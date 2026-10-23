"""Temporary per-channel session audio, aligned to session time (for post-processing)."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

from .types import SAMPLE_RATE, AudioFrame, Channel

_NAMES = {Channel.MIC: "mic", Channel.SYSTEM: "system"}


def _chunks(audio_dir: Path, channel: Channel) -> list[Path]:
    return sorted(Path(audio_dir).glob(f"{_NAMES[channel]}-*.flac"))


class _ChannelWriter:
    def __init__(self, audio_dir: Path, channel: Channel, chunk: int) -> None:
        self.dir, self.channel, self.chunk = audio_dir, channel, chunk
        existing = _chunks(audio_dir, channel)
        self.written = sum(sf.info(str(p)).frames for p in existing)  # samples since session origin
        self.idx = len(existing)
        self.file: sf.SoundFile | None = None
        self.in_file = 0

    def _open(self) -> None:
        path = self.dir / f"{_NAMES[self.channel]}-{self.idx:04d}.flac"
        self.file = sf.SoundFile(str(path), "w", SAMPLE_RATE, 1, format="FLAC", subtype="PCM_16")
        self.idx += 1
        self.in_file = 0

    def write(self, x: np.ndarray) -> None:
        while len(x):
            if self.file is None or self.in_file >= self.chunk:
                self.close()
                self._open()
            n = min(len(x), self.chunk - self.in_file)
            self.file.write(np.clip(x[:n], -1.0, 1.0))
            self.in_file += n
            self.written += n
            x = x[n:]

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None


class AudioRecorder:
    def __init__(self, audio_dir: Path, origin_ns: int, chunk_s: float = 60) -> None:
        self.dir = Path(audio_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.origin_ns = origin_ns
        self._chunk = int(chunk_s * SAMPLE_RATE)
        self._writers: dict[Channel, _ChannelWriter] = {}

    def write(self, frame: AudioFrame) -> None:
        w = self._writers.get(frame.channel)
        if w is None:
            w = self._writers[frame.channel] = _ChannelWriter(self.dir, frame.channel, self._chunk)
        pos = round((frame.t_ns - self.origin_ns) * SAMPLE_RATE / 1e9)
        x = frame.samples.astype(np.float32)
        if pos > w.written:  # gap: pad with silence so file time == session time
            w.write(np.zeros(pos - w.written, np.float32))
        elif pos < w.written:  # overlap/stale: drop what we already have
            x = x[w.written - pos :]
        w.write(x)

    def close(self) -> None:
        for w in self._writers.values():
            w.close()


def load_channel(audio_dir: Path, channel: Channel) -> np.ndarray:
    parts = [sf.read(str(p), dtype="float32")[0] for p in _chunks(audio_dir, channel)]
    return np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32)


def delete_audio(audio_dir: Path) -> None:
    shutil.rmtree(audio_dir, ignore_errors=True)
