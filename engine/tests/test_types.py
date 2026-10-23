import numpy as np

from ontrack.types import Channel, Line, Speaker, Utterance


def test_line_json_roundtrip_keeps_unicode():
    line = Line(seq=3, speaker=Speaker.OTHER, t_start=1.25, t_end=2.5, lang="ru", text="Привет")
    s = line.to_json()
    assert "Привет" in s and "\n" not in s
    assert s.startswith('{"seq":3,"speaker":"Other"')
    assert Line.from_json(s) == line


def test_utterance_duration():
    u = Utterance(Channel.MIC, 1.0, 3.5, np.zeros(40000, np.float32))
    assert u.duration == 2.5
