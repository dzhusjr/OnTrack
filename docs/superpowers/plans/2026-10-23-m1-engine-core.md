# OnTrack M1 — Engine Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python package `ontrack` that turns mic (+ optional system) WAV audio into a live-appended `transcript.jsonl` / `transcript.md` with `Me` / `Other` / `Them` labels, driven from a CLI.

**Architecture:** Small single-purpose modules behind protocols (`Vad`, `AsrBackend`, `Embedder`) so all logic is unit-tested with fakes on any OS; real model adapters (Silero VAD, mlx-whisper, pyannote embeddings) are thin and covered by `models`-marked integration tests that run only on the Mac. A synchronous `Pipeline` wires frames → segmenter → transcriber → filters → labeler → session writer. Threading, sockets to the Swift app, Q&A and post-processing are later milestones.

**Tech Stack:** Python 3.12, uv, numpy, soundfile, soxr, pytest; extras `models`: mlx-whisper (darwin only), silero-vad, pyannote.audio.

**Spec:** `docs/superpowers/specs/2026-10-23-ontrack-design.md` (sections 4.2, 5, 7, 8, 10, 11 apply to M1).

## Global Constraints

- Python `>=3.12`; package root `engine/`, source `engine/src/ontrack/`, tests `engine/tests/`.
- Sample rate inside the engine: **16 000 Hz mono float32**. Frames are 20 ms (320 samples).
- Audio frame wire format (spec §5): little-endian `magic:u16=0x4F54 | channel:u8 | flags:u8 | t_ns:u64 | n:u32 | n×float32` → struct `"<HBBQI"`, 16-byte header.
- Channels: `0` = mic, `1` = system. Speakers render exactly as `Me`, `Other`, `Them`.
- Allowed languages: `("en", "ru", "uk")`. Whisper model id: `mlx-community/whisper-large-v3-turbo`.
- Segmenter: pre-pad 200 ms, end after 600 ms silence, max utterance 15 s, split at lowest-energy 100 ms window within the last 3 s.
- Speaker threshold τ default `0.55`; short utterance `< 1.0 s` inherits neighbour label if gap `≤ 1.5 s`.
- Hallucination: reject if (`no_speech_prob > 0.6` and `avg_logprob < -1.0`) or `compression_ratio > 2.4` or blocklisted/empty text.
- Echo: drop non-`Them` line whose token containment in an overlapping (±1 s) `Them` line is `≥ 0.8` (min 3 tokens).
- `transcript.jsonl` is the source of truth: one JSON object per line, flushed + `os.fsync` per append.
- Unit tests must not import mlx, torch, pyannote or silero. Integration tests carry `@pytest.mark.models`.
- **Where tests run:** unit tests anywhere: `cd engine && uv run pytest -m "not models"`. (In the Cowork Linux VM use `UV_PROJECT_ENVIRONMENT=$HOME/.venvs/ontrack` so the Mac's `engine/.venv` is untouched.) Model tests only on the Mac: `cd engine && uv sync --extra models && uv run pytest -m models`.
- Commits end with the Claude co-author trailer used in this repo.

## Review Focus

1. **Silence or room noise only** (e.g. lecture break) → zero transcript lines, no invented text. Test: Task 9 `test_silence_produces_no_lines`.
2. **Non-16 kHz / stereo WAV given to the CLI** (44.1 kHz stereo recordings are common) → resampled and processed, not rejected. Test: Task 2 `test_wav_source_resamples_stereo_44k`.
3. **Session names with Cyrillic, slashes or emoji** → safe, readable folder name; same-minute duplicates don't collide. Test: Task 8 `test_slugify_*`, `test_start_twice_same_minute_gets_suffix`.
4. **Process killed mid-append** → partial trailing JSON line discarded on resume, `seq` continues without gaps or duplicates. Test: Task 8 `test_resume_discards_partial_trailing_line`.
5. **Lecturer talks > 15 s without pausing** → split into ≤ 15 s pieces with no audio lost or duplicated. Test: Task 3 `test_long_speech_split_preserves_all_samples`.

---

### Task 1: Package scaffold, core types, config

**Files:**
- Create: `engine/pyproject.toml`, `engine/src/ontrack/__init__.py`, `engine/src/ontrack/types.py`, `engine/src/ontrack/config.py`, `engine/tests/conftest.py`, `engine/tests/test_types.py`, `engine/tests/test_config.py`, `engine/README.md`

**Interfaces:**
- Produces (`ontrack.types`):
  - `SAMPLE_RATE = 16000`, `FRAME_SAMPLES = 320`
  - `class Channel(IntEnum): MIC = 0; SYSTEM = 1`
  - `class Speaker(str, Enum): ME = "Me"; OTHER = "Other"; THEM = "Them"`
  - `@dataclass(frozen=True) AudioFrame(channel: Channel, t_ns: int, samples: np.ndarray)` (float32, 1-D)
  - `@dataclass(frozen=True) Utterance(channel: Channel, t_start: float, t_end: float, samples: np.ndarray)` — seconds since session origin; `duration` property
  - `@dataclass(frozen=True) Transcript(text: str, lang: str, avg_logprob: float, no_speech_prob: float, compression_ratio: float)`
  - `@dataclass(frozen=True) Line(seq: int, speaker: Speaker, t_start: float, t_end: float, lang: str, text: str)` with `to_json() -> str` (compact, `ensure_ascii=False`, keys in that order) and `Line.from_json(s: str) -> Line`
- Produces (`ontrack.config`): `@dataclass(frozen=True) Config` with fields and defaults: `tau=0.55, vad_threshold=0.5, pre_pad_ms=200, end_silence_ms=600, max_utt_s=15.0, split_search_s=3.0, split_window_ms=100, allowed_langs=("en","ru","uk"), whisper_model="mlx-community/whisper-large-v3-turbo", context_lines=2, short_utt_s=1.0, neighbor_gap_s=1.5, echo_containment=0.8, echo_min_tokens=3, echo_window_s=1.0, sessions_dir=Path("~/Desktop/Work/OnTrack/sessions").expanduser(), app_support_dir=Path("~/Library/Application Support/OnTrack").expanduser()`; `load_config(path: Path | None) -> Config`.

- [ ] **Step 1: Write `pyproject.toml`** — hatchling build, `requires-python = ">=3.12"`, deps `numpy>=2`, `soundfile>=0.12`, `soxr>=0.5`; optional `models = ["mlx-whisper>=0.4; sys_platform == 'darwin'", "silero-vad>=5.1", "pyannote.audio>=3.3"]`; dev group `pytest>=8`; script `ontrack = "ontrack.cli:main"`; pytest `markers = ["models: needs real ML models (Mac only)"]`, `testpaths = ["tests"]`.
- [ ] **Step 2: Write the failing tests**

```python
# tests/test_types.py
def test_line_json_roundtrip_keeps_unicode():
    line = Line(seq=3, speaker=Speaker.OTHER, t_start=1.25, t_end=2.5, lang="ru", text="Привет")
    s = line.to_json()
    assert "Привет" in s and "\n" not in s
    assert s.startswith('{"seq":3,"speaker":"Other"')
    assert Line.from_json(s) == line

def test_utterance_duration():
    u = Utterance(Channel.MIC, 1.0, 3.5, np.zeros(40000, np.float32))
    assert u.duration == 2.5

# tests/test_config.py
def test_defaults():
    c = load_config(None)
    assert c.tau == 0.55 and c.allowed_langs == ("en", "ru", "uk") and c.max_utt_s == 15.0

def test_toml_overrides(tmp_path):
    p = tmp_path / "c.toml"; p.write_text('tau = 0.6\nsessions_dir = "/tmp/x"\nallowed_langs = ["en"]\n')
    c = load_config(p)
    assert c.tau == 0.6 and c.sessions_dir == Path("/tmp/x") and c.allowed_langs == ("en",)

def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.toml"; p.write_text("tua = 0.6\n")
    with pytest.raises(ValueError, match="tua"):
        load_config(p)

def test_missing_file_uses_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == load_config(None)
```

- [ ] **Step 3: Run** `uv run pytest tests/test_types.py tests/test_config.py -v` → FAIL (ImportError).
- [ ] **Step 4: Implement `types.py` and `config.py`** — `tomllib`; coerce lists→tuples and path strings→`Path` (with `expanduser`) by field type.
- [ ] **Step 5: Run** same command → PASS.
- [ ] **Step 6: Commit** `feat(engine): scaffold package, core types and config`.

---

### Task 2: Audio framing and audio sources

**Files:**
- Create: `engine/src/ontrack/audio_source.py`, `engine/tests/test_audio_source.py`, `engine/tests/fixtures/golden/audio_frames.bin`

**Interfaces:**
- Consumes: `AudioFrame`, `Channel`, `SAMPLE_RATE`, `FRAME_SAMPLES` (Task 1).
- Produces:
  - `MAGIC = 0x4F54`, `HEADER = struct.Struct("<HBBQI")`, `MAX_SAMPLES = 16000`
  - `encode_frame(frame: AudioFrame) -> bytes`
  - `class FrameDecoder: feed(data: bytes) -> list[AudioFrame]`; attribute `dropped: int`
  - `class WavAudioSource(path: Path, channel: Channel)` with `iter_frames() -> Iterator[AudioFrame]` (t_ns starts at 0, step 20 ms)
  - `class SocketAudioSource(sock_path: Path)` with `iter_frames() -> Iterator[AudioFrame]` (Unix-socket server, single client, ends on EOF)
  - `golden_frames() -> list[AudioFrame]` — the two frames the golden file contains (reused by Swift contract tests in M2)

- [ ] **Step 1: Write the failing tests**

```python
def test_encode_decode_roundtrip_across_chunk_boundaries():
    frames = [AudioFrame(Channel.MIC, 0, np.full(320, 0.5, np.float32)),
              AudioFrame(Channel.SYSTEM, 20_000_000, np.linspace(-1, 1, 320, dtype=np.float32))]
    blob = b"".join(encode_frame(f) for f in frames)
    dec = FrameDecoder(); out = []
    for i in range(0, len(blob), 7):          # pathological chunking
        out += dec.feed(blob[i:i+7])
    assert [(f.channel, f.t_ns) for f in out] == [(Channel.MIC, 0), (Channel.SYSTEM, 20_000_000)]
    np.testing.assert_array_equal(out[1].samples, frames[1].samples)
    assert dec.dropped == 0

def test_header_layout_is_16_bytes_little_endian():
    b = encode_frame(AudioFrame(Channel.SYSTEM, 1, np.zeros(2, np.float32)))
    assert b[:16] == bytes.fromhex("544f" "01" "00" "0100000000000000" "02000000")
    assert len(b) == 16 + 8

def test_decoder_resyncs_after_garbage():
    good = encode_frame(AudioFrame(Channel.MIC, 5, np.zeros(320, np.float32)))
    dec = FrameDecoder()
    out = dec.feed(b"\x00\x01garbage" + good)
    assert len(out) == 1 and out[0].t_ns == 5 and dec.dropped >= 1

def test_decoder_rejects_oversized_n():
    bad = HEADER.pack(MAGIC, 0, 0, 0, MAX_SAMPLES + 1)
    good = encode_frame(AudioFrame(Channel.MIC, 9, np.zeros(320, np.float32)))
    dec = FrameDecoder(); out = dec.feed(bad + good)
    assert [f.t_ns for f in out] == [9] and dec.dropped >= 1

def test_golden_file_matches_encoder():
    golden = Path(__file__).parent / "fixtures/golden/audio_frames.bin"
    assert golden.read_bytes() == b"".join(encode_frame(f) for f in golden_frames())

def test_wav_source_frames_and_timestamps(tmp_path):
    p = tmp_path / "a.wav"; sf.write(p, np.zeros(16000, np.float32), 16000)
    frames = list(WavAudioSource(p, Channel.MIC).iter_frames())
    assert len(frames) == 50 and frames[1].t_ns == 20_000_000
    assert all(f.samples.dtype == np.float32 and len(f.samples) == 320 for f in frames)

def test_wav_source_resamples_stereo_44k(tmp_path):          # Review Focus 2
    p = tmp_path / "s.wav"; sf.write(p, np.zeros((44100, 2), np.float32), 44100)
    frames = list(WavAudioSource(p, Channel.MIC).iter_frames())
    assert abs(len(frames) - 50) <= 1

def test_socket_source_reads_until_eof(tmp_path):
    sock = tmp_path / "a.sock"; src = SocketAudioSource(sock)
    # a client thread polls until `sock` exists, connects, sends 3 encoded 320-sample MIC frames (t_ns 0, 20 ms, 40 ms), closes
    got = list(src.iter_frames())
    assert [f.t_ns for f in got] == [0, 20_000_000, 40_000_000]
```

- [ ] **Step 2: Run** `uv run pytest tests/test_audio_source.py -v` → FAIL.
- [ ] **Step 3: Implement** — decoder keeps a `bytearray`, searches for `MAGIC` bytes `b"TO"` to resync, validates `channel in (0,1)` and `0 < n <= MAX_SAMPLES`; WAV source uses `soundfile.read(dtype="float32")`, mean of channels, `soxr.resample(x, sr, 16000)`, final partial frame zero-padded. `golden_frames()` = the two frames in the first test; write the golden file once from it.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(engine): audio frame protocol and WAV/socket sources`.

---

### Task 3: VAD-driven segmenter

**Files:**
- Create: `engine/src/ontrack/vad.py`, `engine/src/ontrack/segmenter.py`, `engine/tests/fakes.py`, `engine/tests/test_segmenter.py`

**Interfaces:**
- Consumes: `AudioFrame`, `Utterance`, `Channel`, `Config`.
- Produces:
  - `class Vad(Protocol): window: int; def speech_prob(self, window: np.ndarray) -> float` (window = 512 samples, Silero's size)
  - `class Segmenter(channel: Channel, vad: Vad, cfg: Config, origin_ns: int)`: `push(frame: AudioFrame) -> list[Utterance]`, `flush() -> list[Utterance]`
  - `tests/fakes.py`: `class EnergyVad(Vad)` — `window = 512`, returns `1.0` if RMS > 0.01 else `0.0`; helpers `tone(seconds, amp=0.3)`, `silence(seconds)`, `frames(samples, channel, start_ns=0) -> list[AudioFrame]`; plus shared test helpers used by later tasks (each added in the task that first needs it): `utt(channel=Channel.MIC, t_start=0.0, t_end=2.0, amp=0.3) -> Utterance` (constant-amplitude samples), `tr(text, lang="en") -> Transcript` (confident: logprob -0.2, no_speech 0.0, compression 1.0), `fake_backends(transcripts: list[Transcript]) -> Backends` (lang probs `{t.lang: 1.0}`), constants `ME_VP = np.array([1.0, 0, 0])`, `NOW = datetime(2026, 10, 23, 14, 5)`

- [ ] **Step 1: Write the failing tests**

```python
def run(samples, **kw):
    seg = Segmenter(Channel.MIC, EnergyVad(), load_config(None), origin_ns=0)
    out = []
    for f in frames(samples, Channel.MIC): out += seg.push(f)
    return out, seg

def test_single_utterance_after_600ms_silence():
    out, _ = run(np.concatenate([silence(1.0), tone(2.0), silence(1.0)]))
    assert len(out) == 1
    assert out[0].t_start == pytest.approx(0.8, abs=0.04)   # 200 ms pre-pad
    assert out[0].t_end == pytest.approx(3.0, abs=0.1)
    assert out[0].channel == Channel.MIC

def test_not_emitted_before_end_silence_then_flush():
    out, seg = run(np.concatenate([tone(1.0), silence(0.4)]))
    assert out == []
    assert len(seg.flush()) == 1

def test_silence_only_yields_nothing():
    out, seg = run(silence(5.0))
    assert out == [] and seg.flush() == []

def test_long_speech_split_at_quiet_point():
    x = tone(20.0); i = int(13.5 * 16000); x[i:i + 1600] = 0.02   # quieter but still speech
    out, seg = run(x); out += seg.flush()
    assert out[0].t_end == pytest.approx(13.55, abs=0.1)
    assert all(u.duration <= 15.0 + 0.2 for u in out)

def test_long_speech_split_preserves_all_samples():           # Review Focus 5
    out, seg = run(tone(40.0)); out += seg.flush()
    assert len(out) >= 3
    assert sum(len(u.samples) for u in out) == pytest.approx(40.0 * 16000, abs=512)
    for a, b in zip(out, out[1:]):
        assert b.t_start == pytest.approx(a.t_end, abs=1e-3)   # contiguous, no overlap
```

- [ ] **Step 2: Run** `uv run pytest tests/test_segmenter.py -v` → FAIL.
- [ ] **Step 3: Implement `Segmenter`** — accumulate samples into 512-sample VAD windows; state `idle|speech`; ring buffer of the last 200 ms while idle (becomes the pre-pad); in speech, count trailing non-speech windows, end when ≥ 600 ms and emit samples up to the end of the last speech window; time = `(t_ns - origin_ns)/1e9` of the first sample. When the open utterance reaches 15 s: over the last 3 s, compute RMS of consecutive non-overlapping 100 ms windows, cut at the centre of the lowest one, emit the head, keep the tail open (no pre-pad on the tail). `flush()` emits any open utterance.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(engine): VAD segmenter with max-length quiet-point split`.

---

### Task 4: Hallucination filter

**Files:**
- Create: `engine/src/ontrack/hallucination.py`, `engine/tests/test_hallucination.py`

**Interfaces:**
- Consumes: `Transcript`.
- Produces: `BLOCKLIST: frozenset[str]`, `normalize(text: str) -> str` (lowercase, strip punctuation, collapse whitespace), `is_hallucination(t: Transcript) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
T = lambda text, nsp=0.1, lp=-0.3, cr=1.2: Transcript(text, "en", lp, nsp, cr)

@pytest.mark.parametrize("text", ["Thanks for watching!", "  Продолжение следует...", "Спасибо за просмотр.", "Дякую за перегляд!", "", "   "])
def test_blocklisted_or_empty(text):
    assert is_hallucination(T(text))

def test_low_confidence_silence():
    assert is_hallucination(T("hello there", nsp=0.7, lp=-1.2))
    assert not is_hallucination(T("hello there", nsp=0.7, lp=-0.5))

def test_repetitive_output():
    assert is_hallucination(T("the the the", cr=2.5))

def test_real_speech_passes():
    assert not is_hallucination(T("Thanks for watching the demo, now let's look at the budget"))
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; `BLOCKLIST` = normalized forms of: "thanks for watching", "thank you for watching", "please subscribe", "subscribe to the channel", "продолжение следует", "спасибо за просмотр", "субтитры сделал dimatorzok", "субтитры создавал dimatorzok", "редактор субтитров а семкин корректор а егорова", "дякую за перегляд". Match is whole-text equality after `normalize`. **Step 4: Run** → PASS. **Step 5: Commit** `feat(engine): whisper hallucination filter`.

---

### Task 5: Language-restricted transcriber

**Files:**
- Create: `engine/src/ontrack/transcriber.py`, `engine/tests/test_transcriber.py`; extend `engine/tests/fakes.py`

**Interfaces:**
- Consumes: `Utterance`, `Transcript`, `Channel`, `Config`.
- Produces:
  - `class AsrBackend(Protocol)`: `detect_language(samples: np.ndarray) -> dict[str, float]`; `run(samples: np.ndarray, language: str, initial_prompt: str | None) -> Transcript`
  - `class Transcriber(backend: AsrBackend, cfg: Config)`: `transcribe(u: Utterance) -> Transcript`; `remember(channel: Channel, text: str) -> None` (called by the pipeline only for accepted lines)
  - `tests/fakes.py`: `class FakeAsr(AsrBackend)` scripted with a queue of `(lang_probs, Transcript)` and recording each `run` call's `(language, initial_prompt)`

- [ ] **Step 1: Write the failing tests**

```python
def test_picks_most_likely_allowed_language():
    asr = FakeAsr([({"de": 0.6, "ru": 0.3, "en": 0.1}, Transcript("привет", "ru", -0.2, 0.0, 1.0))])
    t = Transcriber(asr, load_config(None)).transcribe(utt())
    assert asr.calls[0][0] == "ru" and t.lang == "ru"

def test_context_prompt_is_last_two_accepted_lines_same_channel():
    asr = FakeAsr([({"en": 1.0}, tr("c"))])
    tx = Transcriber(asr, load_config(None))
    tx.remember(Channel.MIC, "one"); tx.remember(Channel.MIC, "two"); tx.remember(Channel.MIC, "three")
    tx.remember(Channel.SYSTEM, "other channel")
    tx.transcribe(utt(Channel.MIC))
    assert asr.calls[0][1] == "two three"

def test_no_context_gives_none_prompt():
    asr = FakeAsr([({"en": 1.0}, tr("x"))]); Transcriber(asr, load_config(None)).transcribe(utt())
    assert asr.calls[0][1] is None

def test_missing_allowed_lang_in_probs_defaults_to_first_allowed():
    asr = FakeAsr([({"ja": 1.0}, tr("x"))]); Transcriber(asr, load_config(None)).transcribe(utt())
    assert asr.calls[0][0] == "en"
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (context = `deque(maxlen=cfg.context_lines)` per channel, joined by a space). **Step 4: Run** → PASS. **Step 5: Commit** `feat(engine): language-restricted transcriber with per-channel context`.

---

### Task 6: Speaker labeler and enrollment

**Files:**
- Create: `engine/src/ontrack/speaker.py`, `engine/tests/test_speaker.py`; extend `engine/tests/fakes.py`

**Interfaces:**
- Consumes: `Utterance`, `Speaker`, `Channel`, `Config`, `WavAudioSource` is **not** used (enroll reads WAV via `soundfile` + `soxr` directly through a shared helper `load_wav_16k(path) -> np.ndarray` placed in `audio_source.py`).
- Produces:
  - `class Embedder(Protocol): def embed(self, samples: np.ndarray) -> np.ndarray`
  - `cosine(a: np.ndarray, b: np.ndarray) -> float`
  - `class SpeakerLabeler(embedder: Embedder, voiceprint: np.ndarray | None, cfg: Config)`: `label(u: Utterance) -> Speaker`; `has_voiceprint: bool`
  - `load_voiceprint(path: Path) -> np.ndarray | None`
  - `enroll(embedder: Embedder, samples: np.ndarray, out_path: Path, window_s: float = 3.0) -> float` — returns mean self-similarity; `ValueError` if fewer than 3 full windows
  - `tests/fakes.py`: `class FakeEmbedder` — returns a fixed vector chosen by the utterance's constant amplitude (`amp 0.3 → [1,0,0]`, `amp 0.5 → [0,1,0]`, otherwise `[0,0,1]`), and counts calls

- [ ] **Step 1: Write the failing tests**

```python
ME_VP = np.array([1.0, 0, 0])

def test_system_channel_is_them_without_embedding():
    emb = FakeEmbedder(); lab = SpeakerLabeler(emb, ME_VP, load_config(None))
    assert lab.label(utt(Channel.SYSTEM, amp=0.3)) == Speaker.THEM and emb.calls == 0

def test_mic_matches_voiceprint_is_me_else_other():
    lab = SpeakerLabeler(FakeEmbedder(), ME_VP, load_config(None))
    assert lab.label(utt(Channel.MIC, 0.0, 2.0, amp=0.3)) == Speaker.ME
    assert lab.label(utt(Channel.MIC, 5.0, 7.0, amp=0.5)) == Speaker.OTHER

def test_threshold_from_config():
    lab = SpeakerLabeler(FakeEmbedder(), np.array([0.5, 0.866, 0]), replace(load_config(None), tau=0.6))
    assert lab.label(utt(Channel.MIC, 0, 2, amp=0.3)) == Speaker.OTHER   # cos = 0.5 < 0.6

def test_no_voiceprint_labels_mic_as_me():
    lab = SpeakerLabeler(FakeEmbedder(), None, load_config(None))
    assert not lab.has_voiceprint and lab.label(utt(Channel.MIC, amp=0.5)) == Speaker.ME

def test_short_utterance_inherits_recent_neighbour():
    emb = FakeEmbedder(); lab = SpeakerLabeler(emb, ME_VP, load_config(None))
    lab.label(utt(Channel.MIC, 0.0, 2.0, amp=0.5))                     # Other
    assert lab.label(utt(Channel.MIC, 3.0, 3.6, amp=0.3)) == Speaker.OTHER and emb.calls == 1
    assert lab.label(utt(Channel.MIC, 6.0, 6.5, amp=0.3)) == Speaker.ME   # gap 2.4 s > 1.5 s → embeds

def test_enroll_writes_normalized_voiceprint(tmp_path):
    score = enroll(FakeEmbedder(), tone(10.0, amp=0.3), tmp_path / "vp.npy")
    vp = load_voiceprint(tmp_path / "vp.npy")
    assert score == pytest.approx(1.0) and np.linalg.norm(vp) == pytest.approx(1.0)

def test_enroll_too_short(tmp_path):
    with pytest.raises(ValueError):
        enroll(FakeEmbedder(), tone(5.0), tmp_path / "vp.npy")
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(engine): voiceprint speaker labeler and enrollment`.

---

### Task 7: Echo filter

**Files:**
- Create: `engine/src/ontrack/echo.py`, `engine/tests/test_echo.py`

**Interfaces:**
- Consumes: `Line`, `Speaker`, `Config`.
- Produces: `tokens(text: str) -> list[str]` (lowercase word tokens, Unicode-aware); `class EchoFilter(cfg: Config)`: `observe(line: Line) -> None` (keeps `Them` lines from the last 30 s of session time), `is_echo(speaker: Speaker, t_start: float, t_end: float, text: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
def them(t0, t1, text): return Line(1, Speaker.THEM, t0, t1, "en", text)

def test_mic_copy_of_overlapping_them_line_is_echo():
    f = EchoFilter(load_config(None)); f.observe(them(10, 14, "we should ship the release on Friday"))
    assert f.is_echo(Speaker.OTHER, 10.3, 14.2, "ship the release on Friday")

def test_non_overlapping_is_not_echo():
    f = EchoFilter(load_config(None)); f.observe(them(10, 14, "we should ship the release on Friday"))
    assert not f.is_echo(Speaker.OTHER, 20, 22, "ship the release on Friday")

def test_short_or_different_text_is_not_echo():
    f = EchoFilter(load_config(None)); f.observe(them(10, 14, "yes okay sure"))
    assert not f.is_echo(Speaker.ME, 10, 11, "okay")                 # < 3 tokens
    assert not f.is_echo(Speaker.ME, 10, 14, "I disagree with that plan")

def test_them_lines_never_echo_and_cyrillic_tokens_work():
    f = EchoFilter(load_config(None)); f.observe(them(0, 3, "Добрый день коллеги как дела"))
    assert f.is_echo(Speaker.OTHER, 0.5, 3.2, "добрый день, коллеги!")
    assert not f.is_echo(Speaker.THEM, 0, 3, "Добрый день коллеги как дела")
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — containment = `|tokens(mic) ∩ tokens(them)| / |set(tokens(mic))|`; overlap if intervals intersect after widening the `Them` interval by `cfg.echo_window_s`. **Step 4: Run** → PASS. **Step 5: Commit** `feat(engine): echo filter for speaker bleed`.

---

### Task 8: Session writer (crash-safe, resumable)

**Files:**
- Create: `engine/src/ontrack/session.py`, `engine/tests/test_session.py`

**Interfaces:**
- Consumes: `Line`, `Speaker`.
- Produces:
  - `slugify(name: str) -> str` — `re.sub(r"[^\w]+", "-", name.lower()).strip("-")[:40] or "session"`
  - `class SessionWriter`: `@classmethod start(root: Path, name: str, now: datetime) -> SessionWriter`; `@classmethod resume(session_dir: Path) -> SessionWriter`; `append(speaker: Speaker, t_start: float, t_end: float, lang: str, text: str) -> Line`; `close(now: datetime) -> None`; properties `dir: Path`, `id: str`, `next_seq: int`
  - `read_lines(session_dir: Path) -> list[Line]` (ignores an invalid trailing line)
  - Files: `meta.json` `{"id","name","started_at","ended_at":null,"version":1}`; empty `notes.md`; `transcript.jsonl`; `transcript.md`
  - `transcript.md` format, exactly: header `# {name}\n\n_Started {YYYY-MM-DD HH:MM}_\n\n`, then per line `` `HH:MM:SS` **{speaker}:** {text}\n `` (time from `t_start`)

- [ ] **Step 1: Write the failing tests**

```python
NOW = datetime(2026, 10, 23, 14, 5)

def test_start_creates_layout(tmp_path):
    w = SessionWriter.start(tmp_path, "Linear Algebra", NOW)
    assert w.dir.name == "2026-10-23-1405-linear-algebra"
    meta = json.loads((w.dir / "meta.json").read_text())
    assert meta["name"] == "Linear Algebra" and meta["ended_at"] is None
    assert (w.dir / "notes.md").read_text() == ""
    assert (w.dir / "transcript.md").read_text() == "# Linear Algebra\n\n_Started 2026-10-23 14:05_\n\n"

@pytest.mark.parametrize("name,slug", [("Встреча с Анной", "встреча-с-анной"), ("a/b\\c", "a-b-c"), ("🚀🚀", "session"), ("x" * 60, "x" * 40)])
def test_slugify_cases(name, slug):                                      # Review Focus 3
    assert slugify(name) == slug

def test_start_twice_same_minute_gets_suffix(tmp_path):
    a = SessionWriter.start(tmp_path, "Call", NOW); b = SessionWriter.start(tmp_path, "Call", NOW)
    assert b.dir.name == a.dir.name + "-2"

def test_append_writes_jsonl_and_md(tmp_path):
    w = SessionWriter.start(tmp_path, "S", NOW)
    l0 = w.append(Speaker.ME, 3723.4, 3725.0, "en", "Hello")
    w.append(Speaker.OTHER, 3726.0, 3727.0, "ru", "Привет")
    assert l0.seq == 0 and [l.seq for l in read_lines(w.dir)] == [0, 1]
    assert (w.dir / "transcript.md").read_text().endswith("`01:02:03` **Me:** Hello\n`01:02:06` **Other:** Привет\n")

def test_resume_discards_partial_trailing_line(tmp_path):                # Review Focus 4
    w = SessionWriter.start(tmp_path, "S", NOW); w.append(Speaker.ME, 0, 1, "en", "a"); w.append(Speaker.ME, 1, 2, "en", "b")
    with open(w.dir / "transcript.jsonl", "a") as f: f.write('{"seq":2,"speak')
    r = SessionWriter.resume(w.dir)
    assert r.next_seq == 2
    r.append(Speaker.OTHER, 2, 3, "en", "c")
    assert [l.text for l in read_lines(w.dir)] == ["a", "b", "c"]
    assert (w.dir / "transcript.md").read_text().count("**") == 6

def test_close_sets_ended_at(tmp_path):
    w = SessionWriter.start(tmp_path, "S", NOW); w.close(datetime(2026, 10, 23, 15, 0))
    assert json.loads((w.dir / "meta.json").read_text())["ended_at"] == "2026-10-23T15:00:00"
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — append: write `to_json()+"\n"`, `flush()`, `os.fsync()`; md appended the same way. `resume` truncates `transcript.jsonl` to the byte after the last valid line, then regenerates `transcript.md` from `read_lines`. Write `meta.json` via temp file + `os.replace`. **Step 4: Run** → PASS. **Step 5: Commit** `feat(engine): crash-safe resumable session writer`.

---

### Task 9: Pipeline and CLI

**Files:**
- Create: `engine/src/ontrack/pipeline.py`, `engine/src/ontrack/cli.py`, `engine/tests/test_pipeline.py`, `engine/tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `@dataclass Backends(vad_factory: Callable[[], Vad], asr: AsrBackend, embedder: Embedder)`; `real_backends(cfg: Config) -> Backends` (lazy imports from Task 10 modules)
  - `class Pipeline(cfg: Config, backends: Backends, writer: SessionWriter, voiceprint: np.ndarray | None, on_line: Callable[[Line], None] | None = None)`: `process(frame: AudioFrame) -> None`, `finish() -> None`; attribute `latencies: list[float]` (wall seconds from utterance emission to line written)
  - Order per utterance: `transcribe` → `is_hallucination` (drop) → `label` → `echo.is_echo` (drop) → `writer.append` → `transcriber.remember` → `echo.observe` → `on_line`. Origin = first frame's `t_ns`; one `Segmenter` per channel created lazily.
  - `cli.main(argv: list[str] | None = None, backends: Backends | None = None) -> int` with subcommands:
    - `ontrack transcribe MIC_WAV [--system SYS_WAV] --name NAME [--config PATH] [--sessions-dir DIR]` — interleaves both sources by `t_ns`, prints each line as `[HH:MM:SS] Speaker: text`, prints session dir at the end; returns 0
    - `ontrack enroll --wav PATH [--config PATH]` — writes `<app_support_dir>/voiceprint.npy`, prints `Voiceprint saved (self-similarity 0.93)`; returns 0, or 2 with a message if audio < 9 s

- [ ] **Step 1: Write the failing tests**

```python
def make(tmp_path, asr_script, vp=ME_VP):
    cfg = replace(load_config(None), sessions_dir=tmp_path)
    w = SessionWriter.start(tmp_path, "T", NOW)
    return Pipeline(cfg, Backends(EnergyVad, FakeAsr(asr_script), FakeEmbedder()), w, vp), w

def test_end_to_end_labels_and_order(tmp_path):
    p, w = make(tmp_path, [({"en": 1}, tr("hi I'm Roman")), ({"ru": 1}, tr("привет", lang="ru"))])
    for f in frames(np.concatenate([tone(1.5, 0.3), silence(1.0), tone(1.5, 0.5), silence(1.0)]), Channel.MIC): p.process(f)
    p.finish()
    assert [(l.speaker, l.text) for l in read_lines(w.dir)] == [(Speaker.ME, "hi I'm Roman"), (Speaker.OTHER, "привет")]

def test_silence_produces_no_lines(tmp_path):                           # Review Focus 1
    p, w = make(tmp_path, [])
    for f in frames(silence(30.0), Channel.MIC): p.process(f)
    p.finish()
    assert read_lines(w.dir) == []

def test_hallucinated_and_echo_lines_dropped(tmp_path):
    p, w = make(tmp_path, [({"en": 1}, tr("we ship the release on friday")),   # Them
                           ({"en": 1}, tr("we ship the release on friday")),   # mic echo
                           ({"en": 1}, tr("Thanks for watching!"))])
    sys_ = frames(np.concatenate([tone(2.0, 0.3), silence(1.0)]), Channel.SYSTEM)
    mic  = frames(np.concatenate([tone(2.0, 0.5), silence(1.0), tone(1.5, 0.5), silence(1.0)]), Channel.MIC)
    for f in sorted(sys_ + mic, key=lambda f: (f.t_ns, f.channel)): p.process(f)
    p.finish()
    assert [l.speaker for l in read_lines(w.dir)] == [Speaker.THEM]

def test_on_line_callback_and_latency_recorded(tmp_path): ...  # on_line called once per written line; len(p.latencies) == lines written

# tests/test_cli.py
def test_cli_transcribe_writes_session(tmp_path, capsys):
    wav = tmp_path / "m.wav"; sf.write(wav, np.concatenate([tone(1.5), silence(1.0)]), 16000)
    rc = main(["transcribe", str(wav), "--name", "Demo", "--sessions-dir", str(tmp_path / "s")], backends=fake_backends([tr("hello")]))
    out = capsys.readouterr().out
    assert rc == 0 and "[00:00:00] Me: hello" in out and "demo" in out

def test_cli_enroll_too_short(tmp_path, capsys): ...  # 5 s WAV → rc == 2 and "at least" in stderr
```

- [ ] **Step 2: Run** `uv run pytest tests/test_pipeline.py tests/test_cli.py -v` → FAIL.
- [ ] **Step 3: Implement `Pipeline`, then `cli.py`** (`argparse`; no voiceprint file → print one hint line `Tip: run 'ontrack enroll' so OnTrack can tell you apart from others.`).
- [ ] **Step 4: Run the full unit suite** `uv run pytest -m "not models" -q` → all PASS.
- [ ] **Step 5: Commit** `feat(engine): pipeline and ontrack CLI (transcribe, enroll)`.

---

### Task 10: Real model adapters, fixtures and Mac integration tests

**Files:**
- Create: `engine/src/ontrack/vad_silero.py`, `engine/src/ontrack/asr_mlx.py`, `engine/src/ontrack/embed_pyannote.py`, `engine/scripts/make_fixtures.sh`, `engine/tests/fixtures/*.wav`, `engine/tests/test_models_integration.py`
- Modify: `engine/README.md` (setup + HF token instructions)

**Interfaces:**
- Consumes: `Vad`, `AsrBackend`, `Embedder` protocols; `Backends`, `real_backends`.
- Produces: `SileroVad()` (window 512, `silero_vad.load_silero_vad()`), `MlxWhisperBackend(model_id: str)` (`detect_language` via mlx-whisper's decoding `detect_language` on the 30 s log-mel; `run` via `mlx_whisper.transcribe(..., language=, initial_prompt=, condition_on_previous_text=False, word_timestamps=False)` mapping segment stats to `Transcript`: `avg_logprob` mean, `no_speech_prob` max, `compression_ratio` max), `PyannoteEmbedder(token: str | None = os.environ.get("HF_TOKEN"))` using `pyannote/wespeaker-voxceleb-resnet34-LM`, returning an L2-normalised vector.

- [ ] **Step 1: Write `make_fixtures.sh`** (macOS `say` + `afconvert -f WAVE -d LEI16@16000`): `en.wav` (voice Samantha: "Welcome to the meeting. Today we discuss the project budget."), `ru.wav` (Milena: "Добрый день. Сегодня мы обсуждаем бюджет проекта."), `uk.wav` (Lesya: "Добрий день. Сьогодні ми обговорюємо бюджет проекту."), `enroll_daniel.wav` (Daniel, ~30 s of a fixed paragraph), `dialog.wav` (Daniel / Samantha alternating 6 turns, 1 s silence between, also `dialog_truth.txt` listing `Me`/`Other` per turn with Daniel = Me), `codeswitch.wav` (Samantha en sentence + Milena ru sentence). Run it once on the Mac and commit the WAVs.
- [ ] **Step 2: Write the failing integration tests** (all `@pytest.mark.models`):
  - `test_whisper_detects_languages` — en/ru/uk fixtures → `lang` equals file stem; text contains `"budget"` / `"бюджет"` / `"бюджет"`.
  - `test_codeswitch_gets_two_languages` — `ontrack transcribe codeswitch.wav` via `main` → lines with langs `["en", "ru"]`.
  - `test_me_vs_other_accuracy` — enroll from `enroll_daniel.wav`, transcribe `dialog.wav`, compare labels with `dialog_truth.txt` → accuracy `≥ 0.9`.
  - `test_latency_budget` — on `dialog.wav`, `p90(pipeline.latencies) ≤ 2.0` s (leaves 1 s of the spec's 3 s budget for capture/IPC/UI).
  - `test_silero_silence` — 10 s of zeros through `SileroVad` + `Segmenter` → no utterances.
- [ ] **Step 3: Run on the Mac** `cd engine && uv sync --extra models && HF_TOKEN=... uv run pytest -m models -v` → FAIL (adapters missing).
- [ ] **Step 4: Implement the three adapters and `real_backends`.**
- [ ] **Step 5: Run on the Mac** → PASS. If `test_me_vs_other_accuracy` misses, tune `tau` in `Config` defaults and record the measured value in the README.
- [ ] **Step 6: Manual check on the Mac:** record 1 minute of yourself + a friend with QuickTime, export as `.m4a` → convert with `afconvert`, run `uv run ontrack transcribe rec.wav --name "Smoke test"`; transcript reads sensibly and labels look right.
- [ ] **Step 7: Commit** `feat(engine): mlx-whisper, silero and pyannote adapters with Mac integration tests`.

---

## Next plans (written after M1 ships)

M2 Swift capture + IPC (`SocketAudioSource` + control socket, golden-file contract test), M3 overlay/menu bar/hotkeys, M4 overlay Q&A via `claude -p`, M5 end-of-session flow + post-processing + summary + Cowork project-sync task, M6 hardening/soak.
