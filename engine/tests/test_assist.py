"""Being addressed by name, reply suggestions, project folder, language lock, speaker-echo drop."""
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fakes import ME_VP, NOW, EnergyVad, FakeAsr, FakeEmbedder, LangAsr, frames, silence, tone, tr, utt
from test_engine import SPEECH, Client, dirs, make_service, run_with, typ  # noqa: F401

from ontrack.assist import address_reason, build_suggest_prompt, name_regex
from ontrack.config import load_config
from ontrack.llm import ClaudeRunner
from ontrack.pipeline import Backends, Pipeline
from ontrack.session import SessionWriter, read_lines
from ontrack.transcriber import Transcriber
from ontrack.types import Channel, Line, Speaker

NAMES = name_regex(load_config(None).my_names)


def line(speaker, text, seq=0):
    return Line(seq, speaker, 0.0, 1.0, "uk", text)


@pytest.mark.parametrize("text", ["Роман, скажи про продовження імплементації.", "Романе, ти тут?",
                                  "Рома, глянь", "Roman, what do you think?", "ok ROMAN"])
def test_name_mentions_are_detected(text):
    assert address_reason(line(Speaker.THEM, text), NAMES, interview=False) == "name"


def test_not_a_name_and_not_from_me():
    assert address_reason(line(Speaker.THEM, "Романтика і романи"), NAMES, False) is None
    assert address_reason(line(Speaker.ME, "Роман тут"), NAMES, False) is None
    assert address_reason(line(Speaker.OTHER, "А що з тестами?"), NAMES, interview=False) is None
    assert address_reason(line(Speaker.OTHER, "А що з тестами?"), NAMES, interview=True) == "question"


def test_suggest_prompt_is_brief_first_person_and_project_aware():
    p = build_suggest_prompt("`00:01` **Them:** hi", "Роман, що по задачі?", "Roman", "cetl")
    assert "At most 2 short sentences" in p and "first person" in p and '"cetl"' in p and "Роман, що по задачі?" in p


# ---- language -------------------------------------------------------------------------------------
def test_short_utterance_keeps_channel_language_and_ties_prefer_it():
    asr = LangAsr({"en": 0.6, "uk": 0.4}, {"en": ("It's clear.", -0.30), "uk": ("Зрозуміло.", -0.45)})
    tx = Transcriber(asr, load_config(None))
    tx._last_lang[Channel.SYSTEM] = "uk"
    assert tx.transcribe(utt(Channel.SYSTEM, 0, 1.2)).text == "Зрозуміло."  # short: no detection at all
    assert [c[0] for c in asr.calls] == ["uk"]
    asr.calls.clear()
    t = tx.transcribe(utt(Channel.SYSTEM, 0, 4.0))  # long: en+uk decoded; close scores → stay in Ukrainian
    assert t.lang == "uk" and sorted(c[0] for c in asr.calls) == ["en", "uk"]


def test_clear_switch_still_happens():
    asr = LangAsr({"en": 0.95}, {"en": ("Let's talk about the roadmap.", -0.2), "uk": ("Лец ток", -1.4)})
    tx = Transcriber(asr, load_config(None))
    tx._last_lang[Channel.MIC] = "uk"
    assert tx.transcribe(utt(Channel.MIC, 0, 4.0)).lang == "en"


def test_language_lock_decodes_only_that_language():
    asr = LangAsr({"en": 0.99}, {"uk": ("Поїхали.", -0.3), "en": ("Let's go.", -0.1)})
    tx = Transcriber(asr, load_config(None))
    tx.languages = ("uk",)
    assert tx.transcribe(utt()).text == "Поїхали." and [c[0] for c in asr.calls] == ["uk"]


# ---- echo -------------------------------------------------------------------------------------------
def test_mic_pickup_of_call_audio_is_dropped_even_when_text_differs(tmp_path):
    cfg = replace(load_config(None), sessions_dir=tmp_path)
    w = SessionWriter.start(tmp_path, "T", NOW)
    asr = FakeAsr([({"uk": 1}, tr("Зміна паролю профіль вийти.", lang="uk")),
                   ({"en": 1}, tr("As you can see, the last one", lang="en")),  # would be the echo's text
                   ({"uk": 1}, tr("Я на дев'ятій.", lang="uk"))])
    p = Pipeline(cfg, Backends(EnergyVad, asr, FakeEmbedder()), w, ME_VP)
    sys_ = frames(np.concatenate([tone(3.0, 0.3), silence(4.0)]), Channel.SYSTEM)
    # mic: the speakers' echo (other voice, during the call speech), then Roman himself afterwards
    mic = frames(np.concatenate([silence(0.2), tone(2.6, 0.5), silence(1.2), tone(1.5, 0.3), silence(1.5)]), Channel.MIC)
    for f in sorted(sys_ + mic, key=lambda f: (f.t_ns, -int(f.channel))):
        p.process(f)
    p.finish()
    got = [(l.speaker, l.text) for l in read_lines(w.dir)]
    assert got == [(Speaker.THEM, "Зміна паролю профіль вийти."), (Speaker.ME, "As you can see, the last one")]
    assert p.echo_dropped == 1


# ---- engine ---------------------------------------------------------------------------------------
def test_name_mention_sends_addressed_and_a_suggestion(dirs):
    svc, cfg = make_service(dirs, [({"uk": 1}, tr("Роман, скажи про продовження імплементації.", lang="uk"))],
                            runner_script=[["У п'ятницю ", "буде готово."]])
    svc.cfg = replace(cfg, suggest_delay_s=0.0)

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "start_session", "name": "Standup"})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.SYSTEM))
        a = await c.expect(typ("addressed"))
        assert a["reason"] == "name" and a["text"].startswith("Роман")
        d = await c.expect(typ("suggestion_delta"))
        assert d["id"] == f"auto-{a['seq']}"
        await c.expect(typ("suggestion_done"))
        await c.send({"type": "stop_session"})
        st = await c.expect(typ("session_stopped"))
        qa = (cfg.sessions_dir / st["session_id"] / "qa.md").read_text(encoding="utf-8")
        assert "Suggested reply" in qa and "У п'ятницю буде готово." in qa
        await c.close()

    asyncio.run(run_with(svc, body))
    assert "Роман, скажи" in svc.runner.prompts[0]


def test_options_pass_project_folder_to_claude_and_lock_language(dirs, tmp_path):
    project = tmp_path / "cetl"
    project.mkdir()
    svc, cfg = make_service(dirs, [({"en": 0.9, "uk": 0.1}, tr("Поїхали.", lang="uk"))], runner_script=[["ok"]])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "set_session_options", "project_dir": str(project), "languages": ["uk"]})
        await c.send({"type": "start_session", "name": "L"})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.MIC))
        line_msg = await c.expect(typ("line"))
        assert line_msg["lang"] == "uk"
        await c.send({"type": "ask", "id": "q1", "question": "що по задачі?"})
        await c.expect(typ("answer_done"))
        await c.send({"type": "set_session_options", "project_dir": str(tmp_path / "nope")})
        e = await c.expect(lambda m: m["type"] == "error" and m["code"] == "bad_project")
        assert "nope" in e["message"]
        await c.close()

    asyncio.run(run_with(svc, body))
    assert svc.runner.project_dirs == [project] and '"cetl"' in svc.runner.prompts[0]
    assert svc.runner.models == ["haiku"]  # live answers use the fastest model


def test_reply_button_answers_from_the_recent_talk(dirs):
    svc, cfg = make_service(dirs, [({"en": 1}, tr("What is your experience with Kafka?"))], runner_script=[["Three years."]])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "set_session_options", "assist": False})
        await c.send({"type": "start_session", "name": "Interview"})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.SYSTEM))
        await c.expect(typ("line"))
        await c.send({"type": "suggest", "id": "s1"})
        d = await c.expect(typ("suggestion_delta"))
        assert d == {"type": "suggestion_delta", "id": "s1", "text": "Three years."}
        await c.expect(typ("suggestion_done"))
        await c.close()

    asyncio.run(run_with(svc, body))
    p = svc.runner.prompts[0]
    assert "What is your experience with Kafka?" in p and "pressed Reply" in p and "max 50 words" in p and "never use placeholders" in p
    assert svc.runner.models == ["haiku"]


# ---- claude runner in a project ------------------------------------------------------------------------
FAKE = Path(__file__).parent / "fake_claude.py"


def test_runner_uses_project_cwd_and_read_only_tools(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    exe.chmod(0o755)
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "log.json"))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "deltas")
    project = tmp_path / "cetl"
    project.mkdir()
    cfg = replace(load_config(None), claude_path=str(exe))
    "".join(ClaudeRunner(cfg).stream("x", 10, project_dir=project))
    log = json.loads((tmp_path / "log.json").read_text())
    assert Path(log["cwd"]).resolve() == project.resolve() and project.exists()
    argv = log["argv"]
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob,Bash" and "--strict-mcp-config" in argv
    allowed = argv[argv.index("--allowedTools") + 1: argv.index("--max-turns")]
    assert "Bash(git log:*)" in allowed and "Read" in allowed and "Bash" not in allowed  # only read-only commands


def test_trigger_words_from_the_app_replace_the_default_names(dirs):
    svc, cfg = make_service(dirs, [({"en": 1}, tr("Any questions from the backend team?"))], runner_script=[["No."]])

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "set_session_options", "names": ["backend team", "Ромчик"], "assist": False})
        await c.send({"type": "start_session", "name": "Standup"})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.SYSTEM))
        a = await c.expect(typ("addressed"))
        assert a["reason"] == "name"
        await c.close()

    asyncio.run(run_with(svc, body))
    assert svc.runner.prompts == []  # auto-suggest off: highlighted, no reply drafted


def test_runner_passes_the_fast_model(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    exe.chmod(0o755)
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "log.json"))
    cfg = replace(load_config(None), claude_path=str(exe))
    "".join(ClaudeRunner(cfg).stream("x", 10, model="haiku"))
    argv = json.loads((tmp_path / "log.json").read_text())["argv"]
    assert argv[argv.index("--model") + 1] == "haiku"


def test_ask_about_a_saved_session_uses_its_notes_folder(dirs):
    svc, cfg = make_service(dirs, runner_script=[["Friday."]])
    d = cfg.notes_dir / "2026-10-24-2216-standup"
    d.mkdir(parents=True)
    (d / "transcript.md").write_text("`00:01:00` **Them:** Deploy on Friday.\n", encoding="utf-8")
    (d / "summary.md").write_text("## TL;DR\nDeploy Friday.\n", encoding="utf-8")

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "ask", "id": "notes-1", "question": "when is the deploy?", "session_id": d.name})
        delta = await c.expect(typ("answer_delta"))
        assert delta == {"type": "answer_delta", "id": "notes-1", "text": "Friday."}
        await c.expect(typ("answer_done"))
        await c.send({"type": "ask", "id": "notes-2", "question": "x", "session_id": "../etc"})
        await c.expect(lambda m: m["type"] == "error" and m["code"] == "no_session")
        await c.close()

    asyncio.run(run_with(svc, body))
    assert "Deploy on Friday." in svc.runner.prompts[0] and "Deploy Friday." in svc.runner.prompts[0]
    assert "when is the deploy?" in (d / "qa.md").read_text(encoding="utf-8")


def test_parse_claude_mcp_list():
    from ontrack.llm import parse_mcp_list

    out = """Checking MCP server health...

atlassian: https://mcp.atlassian.com/v1/sse (SSE) - ✓ Connected
claude.ai Gmail: https://gmail.mcp.claude.com/mcp - ✓ Connected
broken: npx something - ✗ Failed to connect
"""
    assert parse_mcp_list(out) == ["atlassian", "claude_ai_Gmail"]


def test_connectors_enable_mcp_and_allow_each_server(tmp_path, monkeypatch):
    import ontrack.llm as llm

    monkeypatch.setattr(llm, "mcp_servers", lambda cfg, ttl_s=600.0: ["atlassian"])
    r = ClaudeRunner(load_config(None))
    on = r.project_args(connectors=True)
    assert "--strict-mcp-config" not in on and "mcp__atlassian" in on and on[on.index("--max-turns") + 1] == "10"
    assert "--strict-mcp-config" in r.project_args(connectors=False)
    bare = r.connector_args()
    assert bare[:2] == ("--tools", "") and "mcp__atlassian" in bare


def test_connectors_option_reaches_claude_with_a_read_only_hint(dirs):
    svc, cfg = make_service(dirs, [({"en": 1}, tr("Status of 1549?"))], runner_script=[["In review."]])
    calls = []
    real = svc.runner.stream

    def spy(prompt, timeout_s, **kw):
        calls.append(kw)
        return real(prompt, timeout_s, **{k: v for k, v in kw.items() if k != "connectors"})

    svc.runner.stream = spy
    svc.qa.runner = svc.runner

    async def body():
        c = await Client(cfg).connect()
        await c.send({"type": "set_session_options", "connectors": True, "assist": False})
        await c.send({"type": "start_session", "name": "S"})
        await c.expect(lambda m: m["type"] == "status" and m["state"] == "recording")
        await c.audio(frames(SPEECH, Channel.SYSTEM))
        await c.expect(typ("line"))
        await c.send({"type": "suggest", "id": "r1"})
        await c.expect(typ("suggestion_done"))
        await c.close()

    import ontrack.llm as llm
    llm._MCP_CACHE["claude"] = (1e18, [])  # don't run a real `claude mcp list` in tests
    asyncio.run(run_with(svc, body))
    assert calls and calls[-1].get("connectors") is True
    assert "never create, change, send or delete" in svc.runner.prompts[-1]


def test_name_in_a_line_labelled_me_still_counts_when_it_asks_something():
    # the voiceprint put a nearby voice on "Me": the screenshot case
    assert address_reason(line(Speaker.ME, "Okay, Roman, hello. Tell us how much will be 2 plus 2."), NAMES, False) == "name"
    assert address_reason(line(Speaker.ME, "Роман, скажи статус"), NAMES, False) == "name"
    assert address_reason(line(Speaker.ME, "Hi, I'm Roman, what do you need?"), NAMES, False) is None  # self-intro
    assert address_reason(line(Speaker.ME, "Мене звати Роман, як справи?"), NAMES, False) is None
    assert address_reason(line(Speaker.ME, "Roman here, joining now."), NAMES, False) is None  # no question


@pytest.mark.parametrize("text,expected", [
    ("Tell us how much will be 2 plus 2.", True), ("Скажіть, будь ласка, статус.", True),
    ("Як справи з задачею", True), ("Vertel eens over je project.", True), ("We shipped it yesterday.", False),
    ("Добре, дякую.", False)])
def test_questions_without_question_mark(text, expected):
    from ontrack.assist import is_question

    assert is_question(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("how much is 2 plus 2", False), ("what's the status of 1549?", True), ("is CETL-231 merged?", True),
    ("коли в нас зустріч з клієнтом?", True), ("check my email please", True), ("Добре, поїхали далі", False),
    ("what did he say about the deploy", False)])
def test_connectors_only_when_relevant(text, expected):
    from ontrack.connectors import needs_connectors

    assert needs_connectors(text) is expected
