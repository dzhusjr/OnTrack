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
        on_partial: Callable[[Channel, float, str], None] | None = None,
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
        self.on_partial = on_partial
        self._preview_at: dict[Channel, tuple[float, float]] = {}  # channel -> (utterance start, position)
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
        self._maybe_preview(frame.channel, seg)

    def _maybe_preview(self, channel: Channel, seg: Segmenter) -> None:
        if self.on_partial is None or not self.cfg.live_preview or self._degraded:
            return
        start = seg.open_start_s
        if start is None:
            return
        pos = seg.position_s
        last = self._preview_at.get(channel)
        due = last is None or last[0] != start or pos - last[1] >= self.cfg.preview_interval_s
        if pos - start < self.cfg.preview_min_s or not due:
            return
        self._preview_at[channel] = (start, pos)
        samples = seg.open_samples()
        t = self.transcriber.preview(Utterance(channel, start, pos, samples))
        text = t.text.strip()
        if text and not is_hallucination(t):
            self.on_partial(channel, start, text)

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
        mic_seg = self._segmenters.get(Channel.MIC)
        mic_pos = mic_seg.position_s if mic_seg is not None else 0.0
        w = self.cfg.echo_window_s
        keep: list[Utterance] = []
        for u in self._held:
            ready = sys_seg is None
            if not ready:
                horizon = u.t_end + w
                open_start = sys_seg.open_start_s
                passed = sys_seg.position_s >= horizon and (open_start is None or open_start > horizon)
                # time out on the mic clock too: system audio may stall (screen locked, capture error)
                waited = max(sys_seg.position_s, mic_pos) - u.t_end
                ready = passed or waited >= self.cfg.mic_hold_max_s
            if ready:
                self._handle(u)
            else:
                keep.append(u)
        self._held = keep

    def _handle(self, u: Utterance) -> None:
        if self.on_partial is not None and self.cfg.live_preview:
            self.on_partial(u.channel, u.t_start, "")  # the final line (or nothing) replaces the preview
        t0 = time.perf_counter()
        t = self.transcriber.transcribe(u)
        if is_hallucination(t):
            return
        text = t.text.strip()
        speaker = self.labeler.label(u)
        if self.echo.is_echo(speaker, u.t_start, u.t_end, text):
            return
        line = self.writer.append(speaker, u.t_start, u.t_end, t.lang, text)
        self.transcriber.remember(u.channel, text, t.lang)
        self.echo.observe(line)
        self.latencies.append(time.perf_counter() - t0)
        if self.on_line:
            self.on_line(line)
