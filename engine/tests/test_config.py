from pathlib import Path

import pytest

from ontrack.config import load_config


def test_defaults():
    c = load_config(None)
    assert c.tau == 0.55 and c.allowed_langs == ("en", "ru", "uk") and c.max_utt_s == 15.0


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
