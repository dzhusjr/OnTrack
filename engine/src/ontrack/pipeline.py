"""Wires segmenter → transcriber → filters → labeler → session writer (spec §7.2)."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .config import Config
from .echo import EchoFilter
from .hallucination import is_hallucination, normalize
from .segmenter import Segmenter
from .session import SessionWriter
from .speaker import Embedder, SpeakerLabeler
from .transcriber import AsrBackend, Transcriber
from .types import AudioFrame, Channel, Line, Speaker, Utterance
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
        self.previews_paused = False  # set by the engine while transcription is behind real time
        self._held: list[Utterance] = []  # mic utterances waiting for overlapping system audio
        self._last_text: dict[Channel, tuple[str, float]] = {}  # channel -> (normalized text, t_end)
        self.on_partial = on_partial
        self._preview_at: dict[Channel, tuple[float, float]] = {}  # channel -> (utterance start, position)
        self._origin: int | None = writer.origin_ns  # resumed sessions keep their time origin
        self._segmenters: dict[Channel, Segmenter] = {}
        self._sys_spans: list[tuple[float, float]] = []  # recent call-audio speech, for echo detection
        self.echo_dropped = 0

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
        if self.on_partial is None or not self.cfg.live_preview or self._degraded or self.previews_paused:
            return
        start = seg.open_start_s
        if start is None:
            return
        pos = seg.position_s
        last = self._preview_at.get(channel)
        # each preview re-decodes the whole open phrase, so long phrases preview less often
        interval = max(self.cfg.preview_interval_s, 0.15 * (pos - start))
        due = last is None or last[0] != start or pos - last[1] >= interval
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

    def _overlaps_call(self, u: Utterance) -> float:
        """Fraction of this mic utterance that coincides with speech on the call channel."""
        if u.duration <= 0:
            return 0.0
        covered = sum(max(0.0, min(u.t_end, b) - max(u.t_start, a)) for a, b in self._sys_spans)
        return min(1.0, covered / u.duration)

    def _handle(self, u: Utterance) -> None:
        if self.on_partial is not None and self.cfg.live_preview:
            self.on_partial(u.channel, u.t_start, "")  # the final line (or nothing) replaces the preview
        t0 = time.perf_counter()
        if u.channel == Channel.SYSTEM:
            self._sys_spans = [s for s in self._sys_spans if u.t_start - s[1] < 60.0] + [(u.t_start, u.t_end)]
            speaker = Speaker.THEM
        else:
            speaker = self.labeler.label(u)
            if speaker != Speaker.ME and self._overlaps_call(u) >= self.cfg.echo_overlap:
                self.echo_dropped += 1  # call audio from the speakers picked up by the mic: skip (and save ASR)
                return
        t = self.transcriber.transcribe(u)
        if is_hallucination(t, duration=u.duration):
            return
        text = t.text.strip()
        norm = normalize(text)
        prev = self._last_text.get(u.channel)
        if prev and u.duration < 1.5 and norm == prev[0] and u.t_start - prev[1] < 10.0:
            return  # the same short phrase again right away: repeated hallucination
        if self.echo.is_echo(speaker, u.t_start, u.t_end, text):
            return
        line = self.writer.append(speaker, u.t_start, u.t_end, t.lang, text)
        self._last_text[u.channel] = (norm, u.t_end)
        self.transcriber.remember(u.channel, text, t.lang)
        self.echo.observe(line)
        self.latencies.append(time.perf_counter() - t0)
        if self.on_line:
            self.on_line(line)
