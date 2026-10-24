"""Lag handling: previews pause first, fast mode second, and fast mode doesn't flap."""
from types import SimpleNamespace

import ontrack.engine as eng


class P:
    def __init__(self):
        self.previews_paused = False
        self.degraded = False

    def set_degraded(self, on):
        self.degraded = on


def svc(degraded_lag_s=20.0):
    s = SimpleNamespace(cfg=SimpleNamespace(degraded_lag_s=degraded_lag_s), pipeline=P(), degraded=False,
                        _degraded_since=0.0, fallback_asr=object(), _enqueued_t=0, _processed_t=0,
                        _send_status=lambda: None, _call=lambda fn: fn())
    return s


def lag(s, seconds):
    s._enqueued_t, s._processed_t = int(seconds * 1e9), 0
    eng.EngineService._check_lag(s)


def test_previews_pause_and_resume_with_hysteresis():
    s = svc()
    lag(s, 1.0)
    assert not s.pipeline.previews_paused
    lag(s, 2.0)
    assert s.pipeline.previews_paused and not s.degraded
    lag(s, 1.0)
    assert s.pipeline.previews_paused  # still catching up
    lag(s, 0.2)
    assert not s.pipeline.previews_paused


def test_fast_mode_stays_on_for_a_minimum_time(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(eng.time, "monotonic", lambda: clock[0])
    s = svc()
    lag(s, 25.0)
    assert s.degraded and s.pipeline.degraded
    clock[0] += 5
    lag(s, 0.5)
    assert s.degraded  # caught up quickly, but no flapping back yet
    clock[0] += eng.DEGRADED_MIN_S
    lag(s, 0.5)
    assert not s.degraded and not s.pipeline.degraded
