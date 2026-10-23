import socket
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from ontrack.audio_source import (
    HEADER,
    MAGIC,
    MAX_SAMPLES,
    FrameDecoder,
    SocketAudioSource,
    WavAudioSource,
    encode_frame,
    golden_frames,
)
from ontrack.types import AudioFrame, Channel


def test_encode_decode_roundtrip_across_chunk_boundaries():
    frames = [
        AudioFrame(Channel.MIC, 0, np.full(320, 0.5, np.float32)),
        AudioFrame(Channel.SYSTEM, 20_000_000, np.linspace(-1, 1, 320, dtype=np.float32)),
    ]
    blob = b"".join(encode_frame(f) for f in frames)
    dec = FrameDecoder()
    out = []
    for i in range(0, len(blob), 7):  # pathological chunking
        out += dec.feed(blob[i : i + 7])
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
    dec = FrameDecoder()
    out = dec.feed(bad + good)
    assert [f.t_ns for f in out] == [9] and dec.dropped >= 1


def test_golden_file_matches_encoder():
    golden = Path(__file__).parent / "fixtures/golden/audio_frames.bin"
    assert golden.read_bytes() == b"".join(encode_frame(f) for f in golden_frames())


def test_wav_source_frames_and_timestamps(tmp_path):
    p = tmp_path / "a.wav"
    sf.write(p, np.zeros(16000, np.float32), 16000)
    frames = list(WavAudioSource(p, Channel.MIC).iter_frames())
    assert len(frames) == 50 and frames[1].t_ns == 20_000_000
    assert all(f.samples.dtype == np.float32 and len(f.samples) == 320 for f in frames)


def test_wav_source_resamples_stereo_44k(tmp_path):  # Review Focus 2
    p = tmp_path / "s.wav"
    sf.write(p, np.zeros((44100, 2), np.float32), 44100)
    frames = list(WavAudioSource(p, Channel.MIC).iter_frames())
    assert abs(len(frames) - 50) <= 1


def test_socket_source_reads_until_eof():
    sock = Path(tempfile.mkdtemp(prefix="ot", dir="/tmp")) / "a.sock"  # AF_UNIX path limit ~104 chars
    src = SocketAudioSource(sock)

    def client():
        for _ in range(200):
            if sock.exists():
                break
            time.sleep(0.01)
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.connect(str(sock))
        for i in range(3):
            c.sendall(encode_frame(AudioFrame(Channel.MIC, i * 20_000_000, np.zeros(320, np.float32))))
        c.close()

    threading.Thread(target=client, daemon=True).start()
    got = list(src.iter_frames())
    assert [f.t_ns for f in got] == [0, 20_000_000, 40_000_000]
