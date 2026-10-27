"""Generate integration-test audio with macOS `say` (run on the Mac):
    cd engine && uv run python scripts/make_fixtures.py
Requires voices Samantha, Daniel, Milena, Lesya
(System Settings → Accessibility → Spoken Content → System voice → Manage Voices)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
SR = 16000

ENROLL = (
    "My name is Daniel and this is a recording of my normal speaking voice. I am reading a short paragraph "
    "so that the transcriber can learn what I sound like. Today I will talk about lectures, meetings, "
    "budgets and deadlines. The weather in Ghent is cloudy, but the coffee is good and the trains are "
    "mostly on time. I hope this is long enough to build a reliable voice print for the tests."
)
DIALOG = [
    ("Daniel", "Hi, thanks for joining. Shall we go through the project plan for next week?"),
    ("Samantha", "Sure. I think the main risk is the data migration on Wednesday."),
    ("Daniel", "Agreed. Can you send me the rollback steps before Tuesday evening?"),
    ("Samantha", "Yes, I will write them up tonight and share the document."),
    ("Daniel", "Great. Then I will book a short review meeting on Thursday morning."),
    ("Samantha", "Perfect, Thursday at ten works well for me."),
]


def say(voice: str, text: str) -> np.ndarray:
    with tempfile.TemporaryDirectory() as d:
        aiff, wav = Path(d) / "x.aiff", Path(d) / "x.wav"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1", str(aiff), str(wav)], check=True)
        x, _ = sf.read(wav, dtype="float32")
        return x


def gap(s: float = 1.0) -> np.ndarray:
    return np.zeros(int(s * SR), np.float32)


def main() -> int:
    if sys.platform != "darwin":
        print("Run this on macOS (needs `say` and `afconvert`).", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    w = lambda name, x: sf.write(OUT / name, x, SR)  # noqa: E731
    w("en.wav", np.concatenate([gap(0.5), say("Samantha", "Welcome to the meeting. Today we discuss the project budget."), gap(1)]))
    w("ru.wav", np.concatenate([gap(0.5), say("Milena", "Добрый день. Сегодня мы обсуждаем бюджет проекта."), gap(1)]))
    w("uk.wav", np.concatenate([gap(0.5), say("Lesya", "Добрий день. Сьогодні ми обговорюємо бюджет проекту."), gap(1)]))
    w("enroll_daniel.wav", say("Daniel", ENROLL))
    parts = []
    for voice, text in DIALOG:
        parts += [say(voice, text), gap(1.0)]
    w("dialog.wav", np.concatenate([gap(0.5)] + parts))
    (OUT / "dialog_truth.txt").write_text(" ".join("Me" if v == "Daniel" else "Other" for v, _ in DIALOG) + "\n")
    w("codeswitch.wav", np.concatenate([gap(0.5), say("Samantha", "Let's start with the budget for this quarter."),
                                        gap(1.2), say("Milena", "Хорошо, давайте начнём с бюджета."), gap(1)]))
    print(f"Fixtures written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
