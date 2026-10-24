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


def _decode_tolerant(path: Path) -> np.ndarray:
    """Decode as much of a FLAC chunk as possible (chunks left open by a crash have a bogus length)."""
    parts: list[np.ndarray] = []
    try:
        with sf.SoundFile(str(path)) as f:
            while True:
                try:
                    block = f.read(SAMPLE_RATE, dtype="float32")
                except Exception:
                    break
                if len(block) == 0:
                    break
                parts.append(block.reshape(-1) if block.ndim == 1 else block.mean(axis=1))
    except Exception:
        pass
    return np.concatenate(parts).astype(np.float32, copy=False) if parts else np.zeros(0, np.float32)


def _chunk_frames(path: Path, chunk: int) -> int:
    """Sample count of a chunk; repairs (re-encodes) chunks whose header was never finalised."""
    try:
        frames = sf.info(str(path)).frames
        if 0 <= frames <= 2 * chunk:
            return frames
    except Exception:
        pass
    data = _decode_tolerant(path)
    sf.write(str(path), data, SAMPLE_RATE, format="FLAC", subtype="PCM_16")
    return len(data)


class _ChannelWriter:
    def __init__(self, audio_dir: Path, channel: Channel, chunk: int) -> None:
        self.dir, self.channel, self.chunk = audio_dir, channel, chunk
        existing = _chunks(audio_dir, channel)
        self.written = sum(_chunk_frames(p, chunk) for p in existing)  # samples since session origin
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
    parts = [_decode_tolerant(p) for p in _chunks(audio_dir, channel)]
    return np.concatenate(parts) if parts else np.zeros(0, np.float32)


def delete_audio(audio_dir: Path) -> None:
    shutil.rmtree(audio_dir, ignore_errors=True)
