import asyncio
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fakes import EnergyVad, FakeAsr, FakeEmbedder, FakeRunner, frames, silence, tone, tr

from ontrack.audio_source import encode_frame
from ontrack.config import load_config
from ontrack.control import NdjsonDecoder, encode
from ontrack.engine import EngineService
from ontrack.llm import LlmError
from ontrack.pipeline import Backends
from ontrack.types import Channel


class Diar:
    def diarize(self, samples):
        return []


@pytest.fixture
def dirs(tmp_path):
    sock = Path(tempfile.mkdtemp(prefix="ote", dir="/tmp"))  # AF_UNIX path length limit
    yield sock, tmp_path
    shutil.rmtree(sock, ignore_errors=True)


TEST_NAMES = ("Alex", "Олекса", "Олексо", "Сашко")


def make_service(dirs, asr_script=(), runner_script=(), live_preview=False):
    sock, tmp = dirs
    cfg = replace(load_config(None), app_support_dir=sock, sessions_dir=tmp / "sessions", notes_dir=tmp / "notes",
                  live_preview=live_preview,  # previews consume scripted ASR answers; on only where tested
                  my_names=TEST_NAMES)
    backends = Backends(EnergyVad, FakeAsr(list(asr_script)), FakeEmbedder())
    return EngineService(cfg, backends, FakeRunner(list(runner_script)), diarizer=Diar()), cfg


class Client:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dec = NdjsonDecoder()
        self.inbox = []

    async def connect(self):
        for _ in range(100):
            if (self.cfg.app_support_dir / "control.sock").exists() and (self.cfg.app_support_dir / "audio.sock").exists():
                break
            await asyncio.sleep(0.02)
        self.cr, self.cw = await asyncio.open_unix_connection(str(self.cfg.app_support_dir / "control.sock"))
        self.ar, self.aw = await asyncio.open_unix_connection(str(self.cfg.app_support_dir / "audio.sock"))
        return self

    async def send(self, msg):
        self.cw.write(encode(msg))
        await self.cw.drain()

    async def audio(self, fs):
        for f in fs:
            self.aw.write(encode_frame(f))
        await self.aw.drain()

    async def expect(self, pred, timeout=5.0):
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while True:
            for i, m in enumerate(self.inbox):
                if pred(m):
                    return self.inbox.pop(i)
            remaining = end - loop.time()
            if remaining <= 0:
                raise AssertionError(f"timeout; inbox={self.inbox}")
            try:
                data = await asyncio.wait_for(self.cr.read(65536), remaining)
            except asyncio.TimeoutError:
                continue
            self.inbox += self.dec.feed(data)

    async def close(self):
        self.cw.close()
        self.aw.close()


def typ(t):
    return lambda m: m["type"] == t


def err(code):
    return lambda m: m["type"] == "error" and m["code"] == code


async def run_with(service, body):
    task = asyncio.create_task(service.serve())
    try:
        await body()
    finally:
        await service.shutdown()
        await asyncio.wait_for(task, 5)


SPEECH = np.concatenate([tone(1.5, 0.3), silence(1.0)])


def test_ping_and_initial_status(dirs):
    svc, cfg = make_service(dirs)

    async def body():
        c = await Client(cfg).connect()
        st = await c.expect(typ("status"))
        assert st["state"] == "idle" and st["model_ready"] is True
        await c.send({"type": "ping", "id": "p"})
        assert (await c.expect(typ("pong")))["id"] == "p"
        await c.close()

    asyncio.run(run_with(svc, body))


def test_start_stream_stop_emits_lines_and_session_stopped(dirs):
    svc, cfg = make_service(dirs, [({"en": 1}, tr("hello world"))])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "Lecture"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.MIC, start_ns=7_000_000_000))
        line = await c.expect(typ("line"))
        assert line["text"] == "hello world" and line["speaker"] == "Me" and line["seq"] == 0
        assert line["session_id"] == st["session_id"]
        await c.send({"type": "stop_session"})
        stopped = await c.expect(typ("session_stopped"))
        assert stopped["line_count"] == 1 and stopped["session_id"] == st["session_id"]
        await c.close()

    asyncio.run(run_with(svc, body))


def test_finalize_save_all_emits_progress_and_done(dirs):
    svc, cfg = make_service(dirs, [({"en": 1}, tr("hello"))], [["## TL;DR\nok"]])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.MIC))
        await c.expect(typ("line"))
        await c.send({"type": "stop_session"})
        await c.expect(typ("session_stopped"))
        await c.send({"type": "finalize", "session_id": st["session_id"], "action": "save_all"})
        await c.expect(typ("finalize_progress"))
        done = await c.expect(typ("finalize_done"))
        notes = Path(done["notes_path"])
        assert (notes / "summary.md").read_text() == "## TL;DR\nok" and (notes / "transcript.md").exists()
        assert not (cfg.app_support_dir / "audio" / st["session_id"]).exists()
        await c.close()

    asyncio.run(run_with(svc, body))


def test_ask_streams_answer_and_failure_reports_error(dirs):
    svc, cfg = make_service(dirs, [], [["It is ", "Friday"], LlmError("llm_missing", "no claude")])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.send({"type": "ask", "id": "q1", "question": "When?"})
        d1 = await c.expect(typ("answer_delta"))
        d2 = await c.expect(typ("answer_delta"))
        assert d1["text"] + d2["text"] == "It is Friday"
        assert (await c.expect(typ("answer_done")))["id"] == "q1"
        await c.send({"type": "ask", "id": "q2", "question": "Again?"})
        e = await c.expect(err("llm_missing"))
        assert e["recoverable"] is True
        assert (await c.expect(typ("answer_done")))["id"] == "q2"
        await c.close()

    asyncio.run(run_with(svc, body))


def test_resume_same_session_id_continues_seq_and_time(dirs):  # Review Focus 1
    svc, cfg = make_service(dirs, [({"en": 1}, tr("before crash"))])
    state = {}

    async def first():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        state["id"] = st["session_id"]
        await c.audio(frames(SPEECH, Channel.MIC, start_ns=5_000_000_000))
        await c.expect(typ("line"))
        await c.close()

    asyncio.run(run_with(svc, first))  # shutdown without stop_session == crash-like

    svc2, _ = make_service(dirs, [({"en": 1}, tr("after restart"))])

    async def second():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L", "session_id": state["id"]})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        assert st["session_id"] == state["id"]
        await c.audio(frames(SPEECH, Channel.MIC, start_ns=65_000_000_000))
        note = await c.expect(lambda m: m["type"] == "line" and m["speaker"] == "Note")
        line = await c.expect(lambda m: m["type"] == "line" and m["speaker"] != "Note")
        assert note["seq"] == 1 and line["seq"] == 2 and line["t_start"] == pytest.approx(60.0, abs=0.25)
        await c.close()

    asyncio.run(run_with(svc2, second))


def test_stop_right_after_resume_reports_full_duration(dirs):  # "0 s" end card after an engine restart
    svc, cfg = make_service(dirs, [({"en": 1}, tr("before crash"))])
    state = {}

    async def first():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        state["id"] = st["session_id"]
        await c.audio(frames(SPEECH, Channel.MIC, start_ns=5_000_000_000))
        await c.expect(typ("line"))
        await c.close()

    asyncio.run(run_with(svc, first))
    svc2, _ = make_service(dirs)

    async def second():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L", "session_id": state["id"]})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.send({"type": "stop_session"})  # no audio after the restart
        stopped = await c.expect(typ("session_stopped"))
        assert stopped["duration_s"] == pytest.approx(len(SPEECH) / 16000, abs=0.3)
        await c.close()

    asyncio.run(run_with(svc2, second))


def test_gap_inserts_note_line(dirs):
    svc, cfg = make_service(dirs, [({"en": 1}, tr("a")), ({"en": 1}, tr("b"))])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.MIC))
        await c.expect(lambda m: m["type"] == "line" and m["text"] == "a")
        await c.audio(frames(SPEECH, Channel.MIC, start_ns=20_000_000_000))
        note = await c.expect(lambda m: m["type"] == "line" and m["speaker"] == "Note")
        assert note["text"].startswith("[gap 00:1")
        await c.close()

    asyncio.run(run_with(svc, body))


def test_enroll_flow_writes_voiceprint(dirs):
    svc, cfg = make_service(dirs)

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "enroll_start"})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "enrolling")
        await c.audio(frames(tone(30.5, 0.3), Channel.MIC))
        done = await c.expect(typ("enroll_done"), timeout=10)
        assert done["score"] == pytest.approx(1.0) and (cfg.app_support_dir / "voiceprint.npy").exists()
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "idle")
        await c.close()

    asyncio.run(run_with(svc, body))


def test_unknown_message_returns_error(dirs):
    svc, cfg = make_service(dirs)

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "fly"})
        await c.expect(err("bad_message"))
        await c.close()

    asyncio.run(run_with(svc, body))


def test_resume_after_crash_writes_gap_note(dirs):  # review M2 #1
    svc, cfg = make_service(dirs, [({"en": 1}, tr("before"))])
    state = {}

    async def first():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        state["id"] = st["session_id"]
        await c.audio(frames(SPEECH, Channel.MIC))
        await c.expect(typ("line"))
        await c.close()

    asyncio.run(run_with(svc, first))
    svc2, _ = make_service(dirs, [({"en": 1}, tr("after"))])

    async def second():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L", "session_id": state["id"]})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.MIC, start_ns=30_000_000_000))
        note = await c.expect(lambda m: m["type"] == "line" and m["speaker"] == "Note")
        assert note["text"].startswith("[gap 00:2")
        await c.close()

    asyncio.run(run_with(svc2, second))


def test_summary_failure_reported_before_finalize_done(dirs):  # review M2 #5
    svc, cfg = make_service(dirs, [({"en": 1}, tr("hello"))], [LlmError("llm_auth", "please log in")])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.MIC))
        await c.expect(typ("line"))
        await c.send({"type": "stop_session"})
        await c.expect(typ("session_stopped"))
        await c.send({"type": "finalize", "session_id": st["session_id"], "action": "save_all"})
        e = await c.expect(err("summary_failed"))
        assert "log in" in e["message"]
        await c.expect(typ("finalize_done"))
        await c.close()

    asyncio.run(run_with(svc, body))


def test_finalize_previous_session_while_recording_next(dirs):  # review M2 #6
    svc, cfg = make_service(dirs, [({"en": 1}, tr("first"))], [["## TL;DR\nok"]])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "A"})
        a = (await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording"))["session_id"]
        await c.audio(frames(SPEECH, Channel.MIC))
        await c.expect(typ("line"))
        await c.send({"type": "stop_session"})
        await c.expect(typ("session_stopped"))
        await c.send({"type": "start_session", "name": "B"})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording" and m["session_id"] != a)
        await c.send({"type": "finalize", "session_id": a, "action": "save_all"})
        done = await c.expect(typ("finalize_done"))
        assert done["session_id"] == a and done["notes_path"]
        await c.send({"type": "finalize", "session_id": "../../etc", "action": "discard"})
        await c.expect(err("no_session"))
        await c.close()

    asyncio.run(run_with(svc, body))


def test_shutdown_does_not_remove_a_newer_engines_socket(dirs):  # review M2 #8
    svc, cfg = make_service(dirs)

    async def body():
        await Client(cfg).connect()
        ctrl = cfg.app_support_dir / "control.sock"
        ctrl.unlink()
        ctrl.write_text("newer engine")  # another engine took the path

    asyncio.run(run_with(svc, body))
    assert (cfg.app_support_dir / "control.sock").read_text() == "newer engine"


def test_backlog_is_bounded(dirs):  # review M2 #9
    svc, cfg = make_service(dirs)
    svc.cfg = replace(cfg, max_backlog_s=1.0)
    svc._start_session("L", None)
    for f in frames(tone(5.0), Channel.MIC):  # worker not running: nothing is consumed
        svc._on_frame(f)
    assert svc._work.qsize() <= 50 + 1 and svc.dropped_frames >= 190


def test_locked_asr_serialises_calls():  # review M2 #10
    import threading
    import time

    from ontrack.engine import LockedAsr

    active, peak = [0], [0]

    class Slow:
        def detect_language(self, s):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            active[0] -= 1
            return {"en": 1.0}

        run = None

    asr = LockedAsr(Slow())
    ts = [threading.Thread(target=asr.detect_language, args=(None,)) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert peak[0] == 1


def test_partial_messages_reach_the_app(dirs):
    svc, cfg = make_service(dirs, [({"en": 1}, tr(f"t{i}")) for i in range(20)], live_preview=True)

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(np.concatenate([tone(2.0), silence(1.0)]), Channel.MIC))
        part = await c.expect(lambda m: m["type"] == "partial" and m["text"])
        assert part["session_id"] == st["session_id"] and part["channel"] == 0
        await c.expect(lambda m: m["type"] == "partial" and m["text"] == "")
        await c.expect(typ("line"))
        await c.close()

    asyncio.run(run_with(svc, body))


def test_missing_voiceprint_is_reported_on_start(dirs):
    svc, cfg = make_service(dirs)

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        e = await c.expect(err("no_voiceprint"))
        assert "Enroll" in e["message"]
        await c.close()

    asyncio.run(run_with(svc, body))


def test_finalize_retranscribes_with_the_final_model(dirs):
    sock, tmp = dirs
    cfg = replace(load_config(None), app_support_dir=sock, sessions_dir=tmp / "sessions", notes_dir=tmp / "notes",
                  live_preview=False)
    final = FakeAsr([({"en": 1}, tr("Hello world, properly punctuated."))])
    svc = EngineService(cfg, Backends(EnergyVad, FakeAsr([({"en": 1}, tr("hello world"))]), FakeEmbedder()),
                        FakeRunner([]), diarizer=Diar(), final_asr=final)

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.MIC))
        await c.expect(typ("line"))
        await c.send({"type": "stop_session"})
        await c.expect(typ("session_stopped"))
        await c.send({"type": "finalize", "session_id": st["session_id"], "action": "transcript_only"})
        done = await c.expect(typ("finalize_done"))
        assert "properly punctuated" in (Path(done["notes_path"]) / "transcript.md").read_text()
        assert final.calls
        await c.close()

    asyncio.run(run_with(svc, body))


def test_live_transcription_can_be_paused_while_audio_keeps_recording(dirs):
    svc, cfg = make_service(dirs, [({"en": 1}, tr("first")), ({"en": 1}, tr("after resume"))])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        assert st.get("live") is True
        await c.audio(frames(SPEECH, Channel.MIC))
        await c.expect(lambda m: m["type"] == "line" and m["text"] == "first")
        await c.send({"type": "set_live", "enabled": False})
        await c.expect(lambda m: m["type"] == "status" and m.get("live") is False)
        await c.audio(frames(SPEECH, Channel.MIC, start_ns=2_500_000_000))  # recorded, not transcribed
        await c.audio(frames(silence(10.0), Channel.MIC, start_ns=5_000_000_000))
        await asyncio.sleep(0.5)  # audio and control are separate sockets: let the paused audio arrive first
        await c.send({"type": "set_live", "enabled": True})
        await c.expect(lambda m: m["type"] == "status" and m.get("live") is True)
        await c.audio(frames(SPEECH, Channel.MIC, start_ns=15_000_000_000))
        note = await c.expect(lambda m: m["type"] == "line" and m["speaker"] == "Note")
        assert note["text"].startswith("[live transcription paused")
        line = await c.expect(lambda m: m["type"] == "line" and m["speaker"] != "Note")
        assert line["text"] == "after resume"
        await c.send({"type": "stop_session"})
        await c.expect(typ("session_stopped"))
        from ontrack.recorder import load_channel
        from ontrack.session import paused_ranges

        assert len(load_channel(cfg.app_support_dir / "audio" / st["session_id"], Channel.MIC)) >= 17 * 16000
        (t0, t1), = paused_ranges(cfg.sessions_dir / st["session_id"])
        assert t0 == pytest.approx(2.5, abs=0.1) and t1 == pytest.approx(15.0, abs=0.1)
        await c.close()

    asyncio.run(run_with(svc, body))


def test_pause_recording_drops_audio_and_notes_the_pause(dirs):
    svc, cfg = make_service(dirs, [({"en": 1}, tr("first")), ({"en": 1}, tr("after resume"))])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "L"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        assert st.get("paused") is False
        await c.audio(frames(SPEECH, Channel.MIC))
        await c.expect(lambda m: m["type"] == "line" and m["text"] == "first")
        await c.send({"type": "set_paused", "paused": True})
        await c.expect(lambda m: m["type"] == "status" and m.get("paused") is True)
        loud = tone(20.0, 0.5)  # spoken while paused: must never be recorded or transcribed
        await c.audio(frames(loud, Channel.MIC, start_ns=2_500_000_000))
        await asyncio.sleep(0.5)  # audio and control are separate sockets
        await c.send({"type": "set_paused", "paused": False})
        note = await c.expect(lambda m: m["type"] == "line" and m["speaker"] == "Note")
        assert note["text"].startswith("[recording paused 00:2")
        await c.audio(frames(SPEECH, Channel.MIC, start_ns=23_000_000_000))
        line = await c.expect(lambda m: m["type"] == "line" and m["speaker"] != "Note")
        assert line["text"] == "after resume" and line["t_start"] == pytest.approx(23.0, abs=0.3)
        await c.send({"type": "stop_session"})
        stopped = await c.expect(typ("session_stopped"))
        assert stopped["line_count"] == 3  # first, pause note, after resume (no "[gap]" note)
        from ontrack.recorder import load_channel

        mic = load_channel(cfg.app_support_dir / "audio" / st["session_id"], Channel.MIC)
        paused = mic[int(3.0 * 16000):int(22.0 * 16000)]
        assert len(mic) >= 25 * 16000 and float(np.abs(paused).max()) == 0.0  # silence, timeline kept
        await c.close()

    asyncio.run(run_with(svc, body))


def test_set_paused_when_idle_is_harmless(dirs):
    svc, cfg = make_service(dirs)

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "set_paused", "paused": True})
        st = await c.expect(typ("status"))
        assert st["state"] == "idle" and st["paused"] is False
        await c.close()

    asyncio.run(run_with(svc, body))


def test_finish_later_survives_restart_and_keeps_save_choice(dirs):
    import json
    import threading
    entered, release = threading.Event(), threading.Event()
    svc, cfg = make_service(dirs, [({"en": 1}, tr("hello"))])
    class BlockingDiarizer:
        def diarize(self, samples):
            entered.set()
            assert release.wait(5)
            return []
    svc.diarizer = BlockingDiarizer()
    session_id = None

    async def pause():
        nonlocal session_id
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "Finish later"})
        st = await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        session_id = st["session_id"]
        await c.audio(frames(SPEECH, Channel.MIC))
        await c.expect(typ("line"))
        await c.send({"type": "stop_session"})
        await c.expect(typ("session_stopped"))
        await c.send({"type": "finalize", "session_id": session_id, "action": "transcript_only"})
        assert await asyncio.to_thread(entered.wait, 5)
        await c.send({"type": "defer_processing", "session_id": session_id})
        await c.expect(lambda m: m["type"] == "finalize_progress" and m["stage"] == "deferring")
        release.set()
        await c.expect(typ("processing_deferred"))
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "idle")
        assert json.loads((cfg.sessions_dir / session_id / ".pending_processing").read_text())["action"] == "transcript_only"
        assert list((cfg.app_support_dir / "audio" / session_id).glob("*.flac"))
        assert not (cfg.notes_dir / session_id).exists()
        await c.close()
    asyncio.run(run_with(svc, pause))
    restarted, _ = make_service(dirs)
    async def resume():
        c = await Client(cfg).connect()
        await c.send({"type": "continue_processing", "session_id": session_id})
        done = await c.expect(typ("finalize_done"))
        notes = Path(done["notes_path"])
        assert (notes / "transcript.md").exists()
        assert not (notes / "summary.md").exists()
        assert not (cfg.sessions_dir / session_id / ".pending_processing").exists()
        assert not (cfg.app_support_dir / "audio" / session_id).exists()
        await c.close()
    asyncio.run(run_with(restarted, resume))
