import json
from datetime import datetime

import pytest
from fakes import NOW

from ontrack.session import SessionWriter, read_lines, slugify
from ontrack.types import Speaker


def test_start_creates_layout(tmp_path):
    w = SessionWriter.start(tmp_path, "Linear Algebra", NOW)
    assert w.dir.name == "2026-10-23-1405-linear-algebra" and w.id == w.dir.name
    meta = json.loads((w.dir / "meta.json").read_text())
    assert meta["name"] == "Linear Algebra" and meta["ended_at"] is None and meta["version"] == 1
    assert (w.dir / "notes.md").read_text() == ""
    assert (w.dir / "transcript.md").read_text() == "# Linear Algebra\n\n_Started 2026-10-23 14:05_\n\n"


@pytest.mark.parametrize(
    "name,slug",
    [("Встреча с Анной", "встреча-с-анной"), ("a/b\\c", "a-b-c"), ("🚀🚀", "session"), ("x" * 60, "x" * 40)],
)
def test_slugify_cases(name, slug):  # Review Focus 3
    assert slugify(name) == slug


def test_start_twice_same_minute_gets_suffix(tmp_path):
    a = SessionWriter.start(tmp_path, "Call", NOW)
    b = SessionWriter.start(tmp_path, "Call", NOW)
    assert b.dir.name == a.dir.name + "-2"


def test_append_writes_jsonl_and_md(tmp_path):
    w = SessionWriter.start(tmp_path, "S", NOW)
    l0 = w.append(Speaker.ME, 3723.4, 3725.0, "en", "Hello")
    w.append(Speaker.OTHER, 3726.0, 3727.0, "ru", "Привет")
    assert l0.seq == 0 and [l.seq for l in read_lines(w.dir)] == [0, 1]
    assert (w.dir / "transcript.md").read_text().endswith(
        "`01:02:03` **Me:** Hello\n`01:02:06` **Other:** Привет\n"
    )


def test_resume_discards_partial_trailing_line(tmp_path):  # Review Focus 4
    w = SessionWriter.start(tmp_path, "S", NOW)
    w.append(Speaker.ME, 0, 1, "en", "a")
    w.append(Speaker.ME, 1, 2, "en", "b")
    with open(w.dir / "transcript.jsonl", "a") as f:
        f.write('{"seq":2,"speak')
    r = SessionWriter.resume(w.dir)
    assert r.next_seq == 2
    r.append(Speaker.OTHER, 2, 3, "en", "c")
    assert [l.text for l in read_lines(w.dir)] == ["a", "b", "c"]
    assert (w.dir / "transcript.md").read_text().count("**") == 6


def test_close_sets_ended_at(tmp_path):
    w = SessionWriter.start(tmp_path, "S", NOW)
    w.close(datetime(2026, 10, 23, 15, 0))
    assert json.loads((w.dir / "meta.json").read_text())["ended_at"] == "2026-10-23T15:00:00"
