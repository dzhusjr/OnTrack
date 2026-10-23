"""Wires segmenter → transcriber → filters → labeler → session writer (spec §7.2)."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .config import Config
from .echo import EchoFilter
from .hallucination import is_hallucination
from .segmenter import Segmenter
from .session import SessionWriter
from .speaker import Embedder, SpeakerLabeler
from .transcriber import AsrBackend, Transcriber
from .types import AudioFrame, Channel, Line, Utterance
from .vad import Vad


@dataclass
class Backends:
    vad_factory: Callable[[], Vad]
    asr: AsrBackend
    embedder: Embedder


def real_backends(cfg: Config) -> Backends:
    from .asr_mlx import MlxWhisperBackend
    from .embed_pyannote import PyannoteEmbedder
    from .vad_silero import SileroVad

    return Backends(SileroVad, MlxWhisperBackend(cfg.whisper_model), PyannoteEmbedder())


def merge_key(frame: AudioFrame) -> tuple[int, int]:
    """Order for interleaving channels: by time, system before mic on ties, so a
    system-audio utterance is written before an echo of it on the mic."""
    return (frame.t_ns, -int(frame.channel))


class Pipeline:
    def __init__(
        self,
        cfg: Config,
        backends: Backends,
        writer: SessionWriter,
        voiceprint: np.ndarray | None,
        on_line: Callable[[Line], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.backends = backends
        self.writer = writer
        self.on_line = on_line
        self.transcriber = Transcriber(backends.asr, cfg)
        self.labeler = SpeakerLabeler(backends.embedder, voiceprint, cfg)
        self.echo = EchoFilter(cfg)
        self.latencies: list[float] = []
        self._origin: int | None = None
        self._segmenters: dict[Channel, Segmenter] = {}

    def process(self, frame: AudioFrame) -> None:
        if self._origin is None:
            self._origin = frame.t_ns
        seg = self._segmenters.get(frame.channel)
        if seg is None:
            seg = Segmenter(frame.channel, self.backends.vad_factory(), self.cfg, self._origin)
            self._segmenters[frame.channel] = seg
        for u in seg.push(frame):
            self._handle(u)

    def finish(self) -> None:
        pending: list[Utterance] = []
        for ch in sorted(self._segmenters, reverse=True):  # system first
            pending += self._segmenters[ch].flush()
        for u in sorted(pending, key=lambda u: u.t_start):
            self._handle(u)

    def _handle(self, u: Utterance) -> None:
        t0 = time.perf_counter()
        t = self.transcriber.transcribe(u)
        if is_hallucination(t):
            return
        text = t.text.strip()
        speaker = self.labeler.label(u)
        if self.echo.is_echo(speaker, u.t_start, u.t_end, text):
            return
        line = self.writer.append(speaker, u.t_start, u.t_end, t.lang, text)
        self.transcriber.remember(u.channel, text)
        self.echo.observe(line)
        self.latencies.append(time.perf_counter() - t0)
        if self.on_line:
            self.on_line(line)
