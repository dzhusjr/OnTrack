import json
from pathlib import Path

import pytest

from ontrack.control import REQUIRED, NdjsonDecoder, ProtocolError, encode, validate

GOLDEN = Path(__file__).parent / "fixtures/golden/control_messages.ndjson"


def test_encode_is_compact_single_line_utf8():
    b = encode({"type": "line", "text": "Привет"})
    assert b.endswith(b"\n") and b.count(b"\n") == 1 and "Привет".encode() in b
    assert b == b'{"type":"line","text":"\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82"}\n'


def test_decoder_handles_partial_chunks_and_skips_invalid():
    data = encode({"type": "ping"}) + b"not json\n" + encode({"type": "stop_session"})
    dec = NdjsonDecoder()
    out = []
    for i in range(0, len(data), 5):
        out += dec.feed(data[i : i + 5])
    assert [m["type"] for m in out] == ["ping", "stop_session"] and dec.invalid == 1


def test_validate_rejects_unknown_type_and_missing_fields():
    with pytest.raises(ProtocolError, match="unknown"):
        validate({"type": "fly"})
    with pytest.raises(ProtocolError, match="question"):
        validate({"type": "ask", "id": "1"})
    with pytest.raises(ProtocolError, match="action"):
        validate({"type": "finalize", "session_id": "x", "action": "burn"})
    assert validate({"type": "ask", "id": "1", "question": "q"})["question"] == "q"


def test_golden_file_has_every_type_and_validates():
    msgs = [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert sorted({m["type"] for m in msgs}) == sorted(REQUIRED)
    for m in msgs:
        validate(m)
