from dataclasses import replace

from fakes import LangAsr, utt

from ontrack.config import load_config
from ontrack.transcriber import Transcriber
from ontrack.transcriber import ANCHOR_PROMPTS
from ontrack.types import Channel

WITH_RU = replace(load_config(None), allowed_langs=("en", "nl", "uk", "ru"))


def test_confident_detection_decodes_once():
    asr = LangAsr({"en": 0.95, "ru": 0.03}, {"en": ("hello", -0.2)})
    t = Transcriber(asr, load_config(None)).transcribe(utt())
    assert t.text == "hello" and [c[0] for c in asr.calls] == ["en"]


def test_uncertain_detection_keeps_more_confident_decode():
    # English speech mis-detected as mostly Russian: forced-Russian decode is a translation with poor logprob
    asr = LangAsr({"ru": 0.55, "en": 0.40}, {"ru": ("привет как дела", -1.1), "en": ("hi how are you", -0.25)})
    t = Transcriber(asr, WITH_RU).transcribe(utt())
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
    assert asr.calls[0][1].endswith("let's start") and "Привіт усім" not in asr.calls[0][1]


def test_russian_is_never_output_by_default():
    asr = LangAsr({"ru": 0.97, "uk": 0.02}, {"ru": ("так уж треба", -0.1), "uk": ("також треба", -0.6)})
    t = Transcriber(asr, load_config(None)).transcribe(utt())
    assert t.lang == "uk" and all(c[0] != "ru" for c in asr.calls)


def test_english_anchor_gives_punctuation_and_vocabulary_is_included():
    cfg = replace(load_config(None), vocabulary=("Raccoon Gang", "eigenvalue"))
    asr = LangAsr({"en": 0.99}, {"en": ("hi", -0.2)})
    Transcriber(asr, cfg).transcribe(utt())
    prompt = asr.calls[0][1]
    assert prompt.startswith(ANCHOR_PROMPTS["en"]) and "Raccoon Gang" in prompt and "eigenvalue" in prompt


def test_output_that_only_repeats_the_prompt_is_dropped():
    asr = LangAsr({"en": 0.99}, {"en": (ANCHOR_PROMPTS["en"], -0.2)})
    assert Transcriber(asr, load_config(None)).transcribe(utt()).text == ""


def test_confident_russian_is_still_checked_against_ukrainian():  # Whisper is over-confident "ru" on Ukrainian speech
    asr = LangAsr({"ru": 0.92, "uk": 0.05}, {"ru": ("так уж треба заброневат", -0.30), "uk": ("також треба забронювати", -0.55)})
    t = Transcriber(asr, load_config(None)).transcribe(utt())
    assert t.lang == "uk" and t.text == "також треба забронювати"


def test_clear_russian_stays_russian():
    asr = LangAsr({"ru": 0.92, "uk": 0.05}, {"ru": ("добрый день коллеги", -0.20), "uk": ("добри день колеги", -0.95)})
    assert Transcriber(asr, WITH_RU).transcribe(utt()).lang == "ru"  # only when Russian is explicitly allowed


def test_ukrainian_decode_gets_ukrainian_anchor_prompt_without_context():
    asr = LangAsr({"uk": 0.99}, {"uk": ("привіт", -0.2)})
    Transcriber(asr, load_config(None)).transcribe(utt())
    assert asr.calls[0] == ("uk", ANCHOR_PROMPTS["uk"])


def test_fragment_of_the_prompt_is_dropped():  # "Hello, welcome." / "Let's..." echoed on near-silence
    for echo in ["Hello, welcome.", "Let's...", "Let's begin."]:
        asr = LangAsr({"en": 0.99}, {"en": (echo, -0.3)})
        assert Transcriber(asr, load_config(None)).transcribe(utt()).text == "", echo


def test_short_utterances_get_no_prompt():  # prompts make Whisper invent text on sub-second blips
    asr = LangAsr({"en": 0.99}, {"en": ("Yes.", -0.2)})
    t = Transcriber(asr, load_config(None)).transcribe(utt(t_start=0.0, t_end=0.6))
    assert t.text == "Yes." and asr.calls[0][1] is None
