from ontrack.config import load_config
from ontrack.echo import EchoFilter, tokens
from ontrack.types import Line, Speaker


def them(t0, t1, text):
    return Line(1, Speaker.THEM, t0, t1, "en", text)


def test_tokens_unicode():
    assert tokens("Добрый день, коллеги!") == ["добрый", "день", "коллеги"]


def test_mic_copy_of_overlapping_them_line_is_echo():
    f = EchoFilter(load_config(None))
    f.observe(them(10, 14, "we should ship the release on Friday"))
    assert f.is_echo(Speaker.OTHER, 10.3, 14.2, "ship the release on Friday")


def test_non_overlapping_is_not_echo():
    f = EchoFilter(load_config(None))
    f.observe(them(10, 14, "we should ship the release on Friday"))
    assert not f.is_echo(Speaker.OTHER, 20, 22, "ship the release on Friday")


def test_short_or_different_text_is_not_echo():
    f = EchoFilter(load_config(None))
    f.observe(them(10, 14, "yes okay sure"))
    assert not f.is_echo(Speaker.ME, 10, 11, "okay")  # < 3 tokens
    assert not f.is_echo(Speaker.ME, 10, 14, "I disagree with that plan")


def test_them_lines_never_echo_and_cyrillic_tokens_work():
    f = EchoFilter(load_config(None))
    f.observe(them(0, 3, "Добрый день коллеги как дела"))
    assert f.is_echo(Speaker.OTHER, 0.5, 3.2, "добрый день, коллеги!")
    assert not f.is_echo(Speaker.THEM, 0, 3, "Добрый день коллеги как дела")


def test_old_them_lines_expire():
    f = EchoFilter(load_config(None))
    f.observe(them(0, 3, "we should ship the release"))
    f.observe(them(40, 41, "unrelated later words here"))
    assert len(f._recent) == 1
