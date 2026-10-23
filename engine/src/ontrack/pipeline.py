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


class Lazy:
    """Builds the wrapped object on first attribute access (models load only when used)."""

    def __init__(self, factory: Callable[[], object]) -> None:
        self._factory = factory
        self._obj = None

    def __getattr__(self, name: str):
        if self.__dict__.get("_obj") is None:
            self._obj = self._factory()
        return getattr(self._obj, name)


def real_backends(cfg: Config) -> Backends:
    def asr():
        from .asr_mlx import MlxWhisperBackend

        return MlxWhisperBackend(cfg.whisper_model)

    def embedder():
        from .embed_pyannote import PyannoteEmbedder

        return PyannoteEmbedder()

    def vad():
        from .vad_silero import SileroVad

        return SileroVad()

    return Backends(vad, Lazy(asr), Lazy(embedder))


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
        fallback_asr: AsrBackend | None = None,
    ) -> None:
        self.cfg = cfg
        self.backends = backends
        self.writer = writer
        self.on_line = on_line
        self.transcriber = Transcriber(backends.asr, cfg)
        self.labeler = SpeakerLabeler(backends.embedder, voiceprint, cfg)
        self.echo = EchoFilter(cfg)
        self.latencies: list[float] = []
        self._fallback_asr = fallback_asr
        self._degraded = False
        self._held: list[Utterance] = []  # mic utterances waiting for overlapping system audio
        self._origin: int | None = writer.origin_ns  # resumed sessions keep their time origin
        self._segmenters: dict[Channel, Segmenter] = {}

    def process(self, frame: AudioFrame) -> None:
        if self._origin is None:
            self._origin = frame.t_ns
            self.writer.set_origin(frame.t_ns)
        seg = self._segmenters.get(frame.channel)
        if seg is None:
            seg = Segmenter(frame.channel, self.backends.vad_factory(), self.cfg, self._origin)
            self._segmenters[frame.channel] = seg
        for u in seg.push(frame):
            if u.channel == Channel.MIC:
                self._held.append(u)
            else:
                self._handle(u)
        self._release_held()

    def finish(self) -> None:
        pending: list[Utterance] = list(self._held)
        self._held.clear()
        for seg in self._segmenters.values():
            pending += seg.flush()
        # system utterances first at equal times so echoes can be recognised
        for u in sorted(pending, key=lambda u: (u.t_start, -int(u.channel))):
            self._handle(u)

    @property
    def degraded(self) -> bool:
        return self._degraded

    def set_degraded(self, on: bool) -> None:
        """Switch live ASR to the fallback model while transcription lags behind."""
        self._degraded = bool(on and self._fallback_asr is not None)
        self.transcriber.backend = self._fallback_asr if self._degraded else self.backends.asr

    def _release_held(self) -> None:
        if not self._held:
            return
        sys_seg = self._segmenters.get(Channel.SYSTEM)
        w = self.cfg.echo_window_s
        keep: list[Utterance] = []
        for u in self._held:
            ready = sys_seg is None
            if not ready:
                horizon = u.t_end + w
                open_start = sys_seg.open_start_s
                passed = sys_seg.position_s >= horizon and (open_start is None or open_start > horizon)
                ready = passed or sys_seg.position_s - u.t_end >= self.cfg.mic_hold_max_s
            if ready:
                self._handle(u)
            else:
                keep.append(u)
        self._held = keep

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
