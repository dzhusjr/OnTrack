from fakes import LangAsr, utt

from ontrack.config import load_config
from ontrack.transcriber import Transcriber
from ontrack.types import Channel


def test_confident_detection_decodes_once():
    asr = LangAsr({"en": 0.95, "ru": 0.03}, {"en": ("hello", -0.2)})
    t = Transcriber(asr, load_config(None)).transcribe(utt())
    assert t.text == "hello" and [c[0] for c in asr.calls] == ["en"]


def test_uncertain_detection_keeps_more_confident_decode():
    # English speech mis-detected as mostly Russian: forced-Russian decode is a translation with poor logprob
    asr = LangAsr({"ru": 0.55, "en": 0.40}, {"ru": ("привет как дела", -1.1), "en": ("hi how are you", -0.25)})
    t = Transcriber(asr, load_config(None)).transcribe(utt())
    assert t.text == "hi how are you" and t.lang == "en"
    assert sorted(c[0] for c in asr.calls) == ["en", "ru", "uk"]  # a Russian candidate is always checked against Ukrainian


def test_ukrainian_preferred_over_russian_on_close_scores():
    asr = LangAsr({"ru": 0.5, "uk": 0.45}, {"ru": ("добрый день", -0.30), "uk": ("добрий день", -0.38)})
    t = Transcriber(asr, load_config(None)).transcribe(utt())
    assert t.lang == "uk" and t.text == "добрий день"


def test_context_prompt_only_uses_same_language_lines():
    asr = LangAsr({"en": 0.99}, {"en": ("next", -0.2)})
    tx = Transcriber(asr, load_config(None))
    tx.remember(Channel.MIC, "Привіт усім", "uk")
    tx.remember(Channel.MIC, "let's start", "en")
    tx.remember(Channel.MIC, "з бюджету", "uk")
    tx.transcribe(utt(Channel.MIC))
    assert asr.calls[0][1] == "let's start"


def test_default_language_order_prefers_ukrainian_over_russian():
    assert load_config(None).allowed_langs == ("en", "nl", "uk", "ru")


def test_confident_russian_is_still_checked_against_ukrainian():  # Whisper is over-confident "ru" on Ukrainian speech
    asr = LangAsr({"ru": 0.92, "uk": 0.05}, {"ru": ("так уж треба заброневат", -0.30), "uk": ("також треба забронювати", -0.55)})
    t = Transcriber(asr, load_config(None)).transcribe(utt())
    assert t.lang == "uk" and t.text == "також треба забронювати"


def test_clear_russian_stays_russian():
    asr = LangAsr({"ru": 0.92, "uk": 0.05}, {"ru": ("добрый день коллеги", -0.20), "uk": ("добри день колеги", -0.95)})
    assert Transcriber(asr, load_config(None)).transcribe(utt()).lang == "ru"


def test_ukrainian_decode_gets_ukrainian_anchor_prompt_without_context():
    asr = LangAsr({"uk": 0.99}, {"uk": ("привіт", -0.2)})
    Transcriber(asr, load_config(None)).transcribe(utt())
    assert asr.calls[0] == ("uk", "Розмова українською мовою.")
