"""Audio frame wire protocol (spec §5) and audio sources."""
from __future__ import annotations

import os
import socket
import struct
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from .types import FRAME_SAMPLES, SAMPLE_RATE, AudioFrame, Channel

MAGIC = 0x4F54
HEADER = struct.Struct("<HBBQI")
MAX_SAMPLES = 16000
_MAGIC_BYTES = struct.pack("<H", MAGIC)


def encode_frame(frame: AudioFrame) -> bytes:
    samples = np.ascontiguousarray(frame.samples, dtype="<f4")
    return HEADER.pack(MAGIC, int(frame.channel), 0, frame.t_ns, len(samples)) + samples.tobytes()


class FrameDecoder:
    """Incremental decoder; resyncs on garbage and counts dropped frames."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.dropped = 0

    def feed(self, data: bytes) -> list[AudioFrame]:
        self._buf += data
        out: list[AudioFrame] = []
        while True:
            idx = self._buf.find(_MAGIC_BYTES)
            if idx < 0:
                # keep a possible partial magic byte at the end
                if len(self._buf) > 1:
                    self.dropped += 1
                    del self._buf[:-1]
                return out
            if idx > 0:
                self.dropped += 1
                del self._buf[:idx]
            if len(self._buf) < HEADER.size:
                return out
            _, channel, _flags, t_ns, n = HEADER.unpack_from(self._buf)
            if channel not in (0, 1) or not (0 < n <= MAX_SAMPLES):
                self.dropped += 1
                del self._buf[:1]  # skip this magic and rescan
                continue
            end = HEADER.size + 4 * n
            if len(self._buf) < end:
                return out
            samples = np.frombuffer(bytes(self._buf[HEADER.size : end]), dtype="<f4").astype(np.float32)
            out.append(AudioFrame(Channel(channel), t_ns, samples))
            del self._buf[:end]


def golden_frames() -> list[AudioFrame]:
    """Frames stored in tests/fixtures/golden/audio_frames.bin (shared with Swift tests)."""
    return [
        AudioFrame(Channel.MIC, 0, np.full(320, 0.5, np.float32)),
        AudioFrame(Channel.SYSTEM, 20_000_000, np.linspace(-1, 1, 320, dtype=np.float32)),
    ]


def load_wav_16k(path: Path) -> np.ndarray:
    """Read any soundfile-supported file as 16 kHz mono float32."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1).astype(np.float32)
    if sr != SAMPLE_RATE:
        mono = soxr.resample(mono, sr, SAMPLE_RATE).astype(np.float32)
    return mono


class WavAudioSource:
    def __init__(self, path: Path, channel: Channel) -> None:
        self.path = Path(path)
        self.channel = channel

    def iter_frames(self) -> Iterator[AudioFrame]:
        x = load_wav_16k(self.path)
        pad = (-len(x)) % FRAME_SAMPLES
        if pad:
            x = np.concatenate([x, np.zeros(pad, np.float32)])
        step_ns = FRAME_SAMPLES * 1_000_000_000 // SAMPLE_RATE
        for i in range(0, len(x), FRAME_SAMPLES):
            yield AudioFrame(self.channel, (i // FRAME_SAMPLES) * step_ns, x[i : i + FRAME_SAMPLES].copy())


class SocketAudioSource:
    """Unix-socket server accepting a single client; iteration ends on client EOF."""

    def __init__(self, sock_path: Path) -> None:
        self.sock_path = Path(sock_path)
        self.decoder = FrameDecoder()

    def iter_frames(self) -> Iterator[AudioFrame]:
        if self.sock_path.exists():
            os.unlink(self.sock_path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(str(self.sock_path))
            srv.listen(1)
            conn, _ = srv.accept()
            with conn:
                while chunk := conn.recv(65536):
                    yield from self.decoder.feed(chunk)
        finally:
            srv.close()
            if self.sock_path.exists():
                os.unlink(self.sock_path)
