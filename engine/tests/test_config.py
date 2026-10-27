from pathlib import Path

import pytest

from ontrack.config import load_config


def test_defaults():
    c = load_config(None)
    assert c.tau == 0.55 and c.allowed_langs == ("en", "nl", "uk") and c.max_utt_s == 15.0


def test_toml_overrides(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('tau = 0.6\nsessions_dir = "/tmp/x"\nallowed_langs = ["en"]\n')
    c = load_config(p)
    assert c.tau == 0.6 and c.sessions_dir == Path("/tmp/x") and c.allowed_langs == ("en",)


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("tua = 0.6\n")
    with pytest.raises(ValueError, match="tua"):
        load_config(p)


def test_missing_file_uses_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == load_config(None)


def test_full_system_defaults():
    c = load_config(None)
    assert c.notes_dir.name == "notes" and c.llm_model == "sonnet" and c.claude_path == ""
    assert c.qa_timeout_s == 60 and c.summary_timeout_s == 300
    assert c.degraded_lag_s == 20.0 and c.mic_hold_max_s == 15.0 and c.gap_marker_s == 10.0
    assert c.overlay_lines == 3 and c.retranscribe is True
    assert c.fallback_whisper_model == "mlx-community/whisper-small-mlx"


def test_bool_override(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("retranscribe = false\n")
    assert load_config(p).retranscribe is False


def test_accuracy_defaults():
    c = load_config(None)
    assert c.final_whisper_model == "mlx-community/whisper-large-v3-mlx"
    assert c.vocabulary == () and c.post_pad_ms == 200


def test_vocabulary_from_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('vocabulary = ["the university", "eigenvalue", "Acme"]\n')
    assert load_config(p).vocabulary == ("the university", "eigenvalue", "Acme")
