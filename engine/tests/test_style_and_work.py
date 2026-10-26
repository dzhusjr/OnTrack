"""Replies sound like Roman (his collected lines), match the talk's language, and know his recent work."""
import json
import subprocess
from pathlib import Path

from ontrack.assist import build_reply_prompt
from ontrack.style import SpeechStyle, conversation_language, style_block
from ontrack.types import Line, Speaker
from ontrack.workdigest import WorkDigest, find_repos


def ln(speaker, text, lang="uk", seq=0):
    return Line(seq, speaker, 0.0, 1.0, lang, text)


def test_collects_only_my_real_sentences_and_persists(tmp_path):
    st = SpeechStyle(tmp_path / "my_speech.jsonl")
    st.add(ln(Speaker.ME, "Сьогодні я доробив пошук по статтях."))
    st.add(ln(Speaker.ME, "Сьогодні я доробив пошук по статтях."))  # duplicate
    st.add(ln(Speaker.ME, "Так, окей."))  # too short to say anything about style
    st.add(ln(Speaker.THEM, "Роман, що ти сьогодні зробив?"))  # not me
    st.add(ln(Speaker.ME, "I think we should move it to review.", lang="en"))
    again = SpeechStyle(tmp_path / "my_speech.jsonl")
    assert again.samples("uk", 5) == ["Сьогодні я доробив пошук по статтях.", "I think we should move it to review."]
    assert len((tmp_path / "my_speech.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_prefers_examples_in_the_talks_language_when_there_are_enough(tmp_path):
    st = SpeechStyle(tmp_path / "s.jsonl")
    for i in range(6):
        st.add(ln(Speaker.ME, f"Я думаю що варіант номер {i} кращий", seq=i))
    st.add(ln(Speaker.ME, "Let us ship it on Friday then", lang="en"))
    assert all("варіант" in t for t in st.samples("uk", 5))


def test_backfills_from_earlier_sessions(tmp_path):
    s = tmp_path / "sessions" / "2026-10-20-1000-x"
    s.mkdir(parents=True)
    rows = [{"seq": 0, "speaker": "Me", "t_start": 0, "t_end": 1, "lang": "uk", "text": "Я вчора закінчив міграцію бази."},
            {"seq": 1, "speaker": "Them", "t_start": 1, "t_end": 2, "lang": "uk", "text": "Супер, дякую тобі дуже."}]
    (s / "transcript.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    st = SpeechStyle(tmp_path / "my_speech.jsonl", tmp_path / "sessions")
    assert st.samples("uk", 5) == ["Я вчора закінчив міграцію бази."]


def test_language_and_tone_are_stated():
    lines = [ln(Speaker.THEM, "Добрий день, як у нас статус?"), ln(Speaker.OTHER, "ok", lang="en")]
    assert conversation_language(lines) == "uk"
    b = style_block("Roman", "Ukrainian", ["Я думаю, що варто почати з тестів."])
    assert "Reply in Ukrainian" in b and "formal" in b and "Я думаю, що варто почати з тестів." in b


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def test_work_digest_lists_my_recent_commits_and_uncommitted_files(tmp_path):
    work = tmp_path / "Work"
    repo = work / "chemonics" / "cetl-search-api"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "roman@example.com")
    _git(repo, "config", "user.name", "Roman")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "Prioritize search results by relevance (1549)")
    (repo / "b.py").write_text("y = 2\n")
    (work / "notes.md").write_text("standup notes\n")
    assert find_repos(work) == [repo]
    text = WorkDigest().get(work)
    assert "cetl-search-api" in text and "Prioritize search results by relevance (1549)" in text
    assert "uncommitted (1): b.py" in text and "notes.md" in text


def test_reply_prompt_carries_work_and_style_and_forbids_templates():
    p = build_reply_prompt("`00:00:05` **Them:** Роман, що ти сьогодні зробив?", "Roman", "Work",
                           work="## cetl-search-api\nMon 17:10  Prioritize search results", style="Reply in Ukrainian.\n")
    assert "<my_recent_work>" in p and "Prioritize search results" in p and "Reply in Ukrainian." in p
    assert "never use placeholders" in p and "never address Roman" in p
