"""Turns a per-channel frame stream into utterances using a VAD (spec §4.2)."""
from __future__ import annotations

import numpy as np

from .config import Config
from .types import SAMPLE_RATE, AudioFrame, Channel, Utterance
from .vad import Vad


class Segmenter:
    def __init__(self, channel: Channel, vad: Vad, cfg: Config, origin_ns: int) -> None:
        self.channel = channel
        self.vad = vad
        self.cfg = cfg
        self.origin_ns = origin_ns
        self._win = vad.window
        self._pre_pad = cfg.pre_pad_ms * SAMPLE_RATE // 1000
        self._end_silence = cfg.end_silence_ms * SAMPLE_RATE // 1000
        self._max = int(cfg.max_utt_s * SAMPLE_RATE)
        self._search = int(cfg.split_search_s * SAMPLE_RATE)
        self._split_win = cfg.split_window_ms * SAMPLE_RATE // 1000
        self._post_pad = cfg.post_pad_ms * SAMPLE_RATE // 1000
        self._tol = SAMPLE_RATE // 10  # 100 ms timestamp jitter tolerance before treating as gap/stale

        self._pos: int | None = None  # absolute sample index of next unprocessed sample
        self._pending = np.zeros(0, np.float32)  # samples not yet forming a full VAD window
        self._ring = np.zeros(0, np.float32)  # pre-pad history while idle
        self._buf: list[np.ndarray] = []  # open utterance
        self._buf_len = 0
        self._start: int | None = None  # absolute sample index where open utterance starts
        self._last_speech_end = 0  # offset within buffer
        self._silence_run = 0

    # ---- public API ---------------------------------------------------
    @property
    def position_s(self) -> float:
        """Session time up to which audio has been VAD-processed."""
        return (self._pos or 0) / SAMPLE_RATE

    @property
    def open_start_s(self) -> float | None:
        """Start time of the utterance currently being collected, or None when idle."""
        return None if self._start is None else self._start / SAMPLE_RATE

    def push(self, frame: AudioFrame) -> list[Utterance]:
        frame_pos = round((frame.t_ns - self.origin_ns) * SAMPLE_RATE / 1e9)
        out: list[Utterance] = []
        if self._pos is None:
            self._pos = frame_pos
        else:
            expected = self._pos + len(self._pending)
            if frame_pos < expected - self._tol:
                return out  # stale or duplicate frame
            if frame_pos > expected + self._tol:
                out += self.flush()  # capture gap: close the open utterance
                self._pos = frame_pos
                self._ring = np.zeros(0, np.float32)
        self._pending = np.concatenate([self._pending, frame.samples.astype(np.float32)])
        while len(self._pending) >= self._win:
            window, self._pending = self._pending[: self._win], self._pending[self._win :]
            out += self._process_window(window)
        return out

    def flush(self) -> list[Utterance]:
        if self._start is None:
            self._pending = np.zeros(0, np.float32)
            return []
        if len(self._pending):
            self._append(self._pending)
            if self.vad.speech_prob(np.pad(self._pending, (0, self._win - len(self._pending)))) >= self.cfg.vad_threshold:
                self._last_speech_end = self._buf_len
            self._pending = np.zeros(0, np.float32)
        u = self._emit(self._padded_end(), keep_tail=False)
        return [u] if u is not None else []

    def open_samples(self) -> np.ndarray | None:
        """Audio of the utterance currently being collected (for live previews), or None when idle."""
        return self._concat() if self._start is not None else None

    # ---- internals ----------------------------------------------------
    def _process_window(self, window: np.ndarray) -> list[Utterance]:
        is_speech = self.vad.speech_prob(window) >= self.cfg.vad_threshold
        win_start = self._pos
        self._pos += len(window)
        out: list[Utterance] = []
        if self._start is None:
            if is_speech:
                self._start = win_start - len(self._ring)
                self._buf, self._buf_len = [], 0
                self._append(self._ring)
                self._append(window)
                self._ring = np.zeros(0, np.float32)
                self._last_speech_end = self._buf_len
                self._silence_run = 0
            else:
                self._ring = np.concatenate([self._ring, window])[-self._pre_pad :]
            return out

        self._append(window)
        if is_speech:
            self._last_speech_end = self._buf_len
            self._silence_run = 0
        else:
            self._silence_run += len(window)

        if self._silence_run >= self._end_silence:
            cut = self._padded_end()
            trailing = self._concat()[max(cut, 0) :]
            u = self._emit(cut, keep_tail=False)
            if u is not None:
                out.append(u)
            self._ring = trailing[-self._pre_pad :]
        elif self._buf_len >= self._max:
            u = self._emit(self._quiet_cut(), keep_tail=True)
            if u is not None:
                out.append(u)
        return out

    def _padded_end(self) -> int:
        """End of speech plus a little trailing audio (VAD fires slightly before the last syllable ends)."""
        if self._last_speech_end <= 0:
            return 0
        return min(self._last_speech_end + self._post_pad, self._buf_len)

    def _append(self, x: np.ndarray) -> None:
        if len(x):
            self._buf.append(x)
            self._buf_len += len(x)

    def _concat(self) -> np.ndarray:
        return np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)

    def _quiet_cut(self) -> int:
        data = self._concat()
        lo = max(0, len(data) - self._search)
        best, best_rms = len(data), np.inf
        for s in range(lo, len(data) - self._split_win + 1, self._split_win):
            rms = float(np.sqrt(np.mean(data[s : s + self._split_win] ** 2)))
            if rms < best_rms:
                best, best_rms = s + self._split_win // 2, rms
        return best

    def _emit(self, cut: int, keep_tail: bool) -> Utterance | None:
        data = self._concat()
        start = self._start
        if cut <= 0:  # nothing but non-speech left after a split: close without emitting
            self._buf, self._buf_len, self._start = [], 0, None
            self._last_speech_end = 0
            self._silence_run = 0
            return None
        utt = Utterance(
            self.channel,
            start / SAMPLE_RATE,
            (start + cut) / SAMPLE_RATE,
            data[:cut].copy(),
        )
        if keep_tail:
            tail = data[cut:]
            self._buf, self._buf_len = ([tail], len(tail)) if len(tail) else ([], 0)
            self._start = start + cut
            self._last_speech_end = max(0, self._last_speech_end - cut)
        else:
            self._buf, self._buf_len, self._start = [], 0, None
            self._last_speech_end = 0
            self._silence_run = 0
        return utt
