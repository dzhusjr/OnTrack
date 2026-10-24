import pytest

from ontrack.hallucination import is_hallucination
from ontrack.types import Transcript


def T(text, nsp=0.1, lp=-0.3, cr=1.2):
    return Transcript(text, "en", lp, nsp, cr)


@pytest.mark.parametrize(
    "text",
    ["Thanks for watching!", "  Продолжение следует...", "Спасибо за просмотр.", "Дякую за перегляд!", "Ondertiteling door de Amara.org gemeenschap", "Bedankt voor het kijken!", "", "   "],
)
def test_blocklisted_or_empty(text):
    assert is_hallucination(T(text))


def test_low_confidence_silence():
    assert is_hallucination(T("hello there", nsp=0.7, lp=-1.2))
    assert not is_hallucination(T("hello there", nsp=0.7, lp=-0.5))


def test_repetitive_output():
    assert is_hallucination(T("the the the", cr=2.5))


def test_real_speech_passes():
    assert not is_hallucination(T("Thanks for watching the demo, now let's look at the budget"))
