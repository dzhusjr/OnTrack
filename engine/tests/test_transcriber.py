from fakes import FakeAsr, tr, utt

from ontrack.config import load_config
from ontrack.transcriber import Transcriber
from ontrack.types import Channel, Transcript


def test_picks_most_likely_allowed_language():
    from dataclasses import replace

    asr = FakeAsr([({"de": 0.6, "ru": 0.3, "en": 0.1}, Transcript("привет", "ru", -0.2, 0.0, 1.0))])
    t = Transcriber(asr, replace(load_config(None), allowed_langs=("en", "uk", "ru"))).transcribe(utt())
    assert asr.calls[0][0] == "ru" and t.lang == "ru"


def test_context_prompt_is_last_two_accepted_lines_same_channel():
    asr = FakeAsr([({"en": 1.0}, tr("c"))])
    tx = Transcriber(asr, load_config(None))
    tx.remember(Channel.MIC, "one")
    tx.remember(Channel.MIC, "two")
    tx.remember(Channel.MIC, "three")
    tx.remember(Channel.SYSTEM, "other channel")
    tx.transcribe(utt(Channel.MIC))
    assert asr.calls[0][1].endswith("two three") and "one" not in asr.calls[0][1]


def test_no_context_gives_language_anchor_prompt():
    from ontrack.transcriber import ANCHOR_PROMPTS

    asr = FakeAsr([({"en": 1.0}, tr("x"))])
    Transcriber(asr, load_config(None)).transcribe(utt())
    assert asr.calls[0][1] == ANCHOR_PROMPTS["en"]


def test_missing_allowed_lang_in_probs_defaults_to_first_allowed():
    asr = FakeAsr([({"ja": 1.0}, tr("x"))])
    Transcriber(asr, load_config(None)).transcribe(utt())
    assert asr.calls[0][0] == "en"
