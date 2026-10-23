import numpy as np
import soundfile as sf
from fakes import fake_backends, silence, tone, tr

from ontrack.cli import main


def cfg_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(f'app_support_dir = "{tmp_path / "support"}"\n')
    return p


def test_cli_transcribe_writes_session(tmp_path, capsys):
    wav = tmp_path / "m.wav"
    sf.write(wav, np.concatenate([tone(1.5), silence(1.0)]), 16000)
    rc = main(
        ["transcribe", str(wav), "--name", "Demo", "--sessions-dir", str(tmp_path / "s"), "--config", str(cfg_file(tmp_path))],
        backends=fake_backends([tr("hello")]),
    )
    out = capsys.readouterr().out
    assert rc == 0 and "[00:00:00] Me: hello" in out and "demo" in out
    assert "ontrack enroll" in out  # no voiceprint → hint


def test_cli_transcribe_with_system_track(tmp_path, capsys):
    mic, sys_ = tmp_path / "m.wav", tmp_path / "s.wav"
    sf.write(mic, np.concatenate([silence(3.0)]), 16000)
    sf.write(sys_, np.concatenate([tone(1.5), silence(1.0)]), 16000)
    rc = main(
        ["transcribe", str(mic), "--system", str(sys_), "--name", "Call", "--sessions-dir", str(tmp_path / "s"), "--config", str(cfg_file(tmp_path))],
        backends=fake_backends([tr("hi from zoom")]),
    )
    assert rc == 0 and "Them: hi from zoom" in capsys.readouterr().out


def test_cli_enroll_writes_voiceprint(tmp_path, capsys):
    wav = tmp_path / "me.wav"
    sf.write(wav, tone(10.0, 0.3), 16000)
    rc = main(["enroll", "--wav", str(wav), "--config", str(cfg_file(tmp_path))], backends=fake_backends([]))
    assert rc == 0 and (tmp_path / "support" / "voiceprint.npy").exists()
    assert "Voiceprint saved (self-similarity 1.00)" in capsys.readouterr().out


def test_cli_enroll_too_short(tmp_path, capsys):
    wav = tmp_path / "me.wav"
    sf.write(wav, tone(5.0), 16000)
    rc = main(["enroll", "--wav", str(wav), "--config", str(cfg_file(tmp_path))], backends=fake_backends([]))
    assert rc == 2 and "at least" in capsys.readouterr().err
