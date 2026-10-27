"""Notices when someone addresses Roman and drafts a short reply (live meeting / interview helper)."""
from __future__ import annotations

import re
from pathlib import Path

from .types import Line, Speaker


def name_regex(names: tuple[str, ...]) -> re.Pattern | None:
    names = tuple(n for n in names if n.strip())
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.IGNORECASE | re.UNICODE)


# "I'm Roman" / "мене звати Роман": you saying your own name is not someone addressing you
_SELF_INTRO = re.compile(
    r"\b(i'?m|i am|my name is|this is|я|мене звати|меня зовут|ik ben|mijn naam is|ik heet)\s*,?\s*$",
    re.IGNORECASE | re.UNICODE)

# questions and requests without a "?" (speech recognition often drops it): "tell us…", "скажи…", "how much…"
_ASKING = re.compile(
    r"(?<!\w)(what|how|why|when|where|who|which|whose|can you|could you|would you|do you|did you|are you|have you|"
    r"tell|explain|describe|walk us|give us|share|any (?:update|news|questions)|"
    r"що|як|чому|коли|де|хто|який|яка|які|скільки|чи|скажи|скажіть|розкажи|розкажіть|поясни|поясніть|опиши|"
    r"дай апдейт|поділись|"
    r"wat|hoe|waarom|wanneer|waar|wie|welke|kun je|kunt u|vertel|leg uit)(?!\w)",
    re.IGNORECASE | re.UNICODE)


def is_question(text: str) -> bool:
    return "?" in text or bool(_ASKING.search(text))


def address_reason(line: Line, names: re.Pattern | None, interview: bool) -> str | None:
    """'name' when someone says your name / a trigger word, 'question' (interview mode) when someone asks
    or requests anything. Your own lines count only when they really address you (the voiceprint can
    mislabel a nearby voice as you), never when you introduce yourself."""
    text = line.text.strip()
    if line.speaker == Speaker.NOTE or not text:
        return None
    if names is not None:
        for m in names.finditer(text):
            if line.speaker == Speaker.ME and _SELF_INTRO.search(text[: m.start()]):
                continue
            if line.speaker == Speaker.ME and not is_question(text):
                continue  # you mentioning yourself in passing
            return "name"
    if interview and line.speaker != Speaker.ME and is_question(text):
        return "question"
    return None


def transcript_tail(session_dir: Path, lines: int) -> str:
    p = Path(session_dir) / "transcript.md"
    if not p.exists():
        return ""
    body = [l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("`")]
    return "\n".join(body[-lines:])


NOTHING = "—"  # the model's way to say "nothing useful": shown as no suggestion at all

_RULES = (
    "Output ONLY the words {me} will say — no intro, no labels, never address {me}, never use placeholders "
    "or brackets like [task]. Never invent facts.\n"
    "ONLY concrete, useful content: facts from <my_recent_work>, his files, git or connected tools (tickets, "
    "statuses, numbers, names, dates, decisions), or specific details from the talk. NO generic filler, soft "
    "skills, pleasantries or obvious lines (\"let's go over these tickets\", \"good question\", \"I'll look "
    "into it\", \"let's sync\"). If you have nothing concrete and useful, output exactly: " + NOTHING + "\n"
    "If it is about his work or status (what he did, progress, plans, blockers), answer concretely from "
    "<my_recent_work> (rephrase commit messages as natural speech; mention 1–3 main things). If nothing relevant "
    "is known, give a short honest reply without made-up details.\n"
    "The transcript is live speech recognition: silently read through misheard words.\n"
)


def latest_block(latest: str) -> str:
    if not latest.strip():
        return ""
    return ("Reply ONLY to these latest lines. The rest of the transcript is background: questions asked earlier "
            "were already answered — never answer them again.\n"
            f"<latest_lines>\n{latest.strip()}\n</latest_lines>\n\n")


def _context(tail: str, me: str, project: str | None, work: str, style: str, connectors: bool = False,
             latest: str = "") -> str:
    from .qa import CONNECTOR_HINT

    project_hint = (f"His project folder is \"{project}\" (you may read files there if a concrete fact is "
                    "missing; prefer the summary below — it is faster).\n") if project else ""
    work_block = f"<my_recent_work>\n{work.strip()}\n</my_recent_work>\n\n" if work.strip() else ""
    return (
        _RULES.format(me=me) + style + project_hint + (CONNECTOR_HINT if connectors else "") +
        f"Speakers: Me = {me}, Other = people in the room, Them = people on the call.\n\n"
        + work_block +
        f"<recent_transcript>\n{tail.strip()}\n</recent_transcript>\n\n"
        + latest_block(latest)
    )


def build_suggest_prompt(tail: str, trigger: str, me: str, project: str | None, work: str = "",
                         style: str = "", connectors: bool = False, latest: str = "") -> str:
    """Someone said Roman's name / a trigger word (or asked a question in interview mode)."""
    return (
        f"You are {me}'s live meeting / interview helper. Someone just addressed {me}:\n"
        f"\"{trigger.strip()}\"\n\n"
        f"Write what {me} says back, in first person. At most 2 short sentences, or a short answer plus up to "
        "2 short bullets. If the line needs no reply, answer just: " + NOTHING + "\n"
        + _context(tail, me, project, work, style, connectors, latest)
    )


def build_reply_prompt(tail: str, me: str, project: str | None, work: str = "", style: str = "",
                       connectors: bool = False, latest: str = "") -> str:
    """The overlay's Reply button: answer the latest question, or say the one thing worth adding."""
    return (
        f"You are {me}'s live helper in a conversation. {me} pressed Reply: write what he says right now.\n"
        "Look at the latest lines (given separately at the end).\n"
        "- If they contain a question (to him or to the group; it may be split over two lines): first the direct "
        "short answer, "
        "then at most 2 short bullets of detail, only if they really help.\n"
        "- If there was no question: only a concrete fact, update or risk from his work or connected tools that "
        "matters to this talk right now. Otherwise output exactly: " + NOTHING + "\n"
        "First person, max 50 words total.\n"
        + _context(tail, me, project, work, style, connectors, latest)
    )


def build_interview_prompt(tail: str, me: str, style: str = "", latest: str = "", trigger: str | None = None) -> str:
    """Interview mode: answer like a strong candidate from general expertise — never from (or limited by)
    the project folder, and never "I haven't done that"."""
    asked = f"The interviewer just said:\n\"{trigger.strip()}\"\n\n" if trigger else ""
    return (
        f"You are {me}'s live interview helper. {me} is a software engineer (Python, Java, C++) in a technical "
        "interview. Write what he answers right now.\n"
        + asked +
        "Answer from solid general engineering knowledge, as an experienced candidate: confident, correct and "
        "practical — the standard approach, the key trade-off, a concrete tool or example. Speak in first person "
        "(\"I'd…\", \"I usually…\", \"the way I'd do it…\"). Never say he hasn't done it, doesn't know, or only "
        "works on something else; never mention his current projects unless the question asks about them. "
        "Don't invent employers, numbers or past events.\n"
        "Format: the direct short answer first (one sentence), then at most 2 short bullets of detail if they help. "
        "Technical substance only — no soft skills, no filler (\"great question\", \"it depends\" without the answer). "
        "Max 60 words. If the latest lines contain no question, output exactly: " + NOTHING + "\n"
        "Output ONLY the words he will say — no intro, no labels, never address him, no placeholders.\n"
        + style +
        "The transcript is live speech recognition: silently read through misheard words.\n"
        f"Speakers: Me = {me}, Other = people in the room, Them = people on the call.\n\n"
        f"<recent_transcript>\n{tail.strip()}\n</recent_transcript>\n\n"
        + latest_block(latest)
    )
