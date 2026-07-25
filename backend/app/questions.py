"""The 100 questions, loaded once from data/seed/character_questions.json.

The character builder tab drives this. Its only output contract is a flat
`{question_text: answer}` dict, which is exactly what compile_identity_card()
and compile_voice_card() consume. That keeps the builder unblocked from
everything downstream, and keeps everything downstream unblocked from the
builder's UI decisions.
"""
from __future__ import annotations

from functools import lru_cache

from app.store import SEED, load_json


@lru_cache(maxsize=1)
def _data() -> dict:
    return load_json(SEED / "character_questions.json")


def all_questions() -> list[dict]:
    return _data()["questions"]


def parts() -> list[dict]:
    """Each part with its questions, for a sectioned interview UI."""
    qs = all_questions()
    out = []
    for p in _data()["parts"]:
        in_part = [q for q in qs if q["part"] == p["key"]]
        out.append({**p, "count": len(in_part),
                    "core_count": sum(1 for q in in_part if q["is_core"]),
                    "questions": in_part})
    return out


def core_questions() -> list[dict]:
    """The 12 that gate a character as usable for dialogue. Ask these first."""
    return [q for q in all_questions() if q["is_core"]]


CORE_TEXTS: list[str] = [q["text"] for q in core_questions()]
BY_TEXT: dict[str, dict] = {q["text"]: q for q in all_questions()}


def next_unanswered(answers: dict[str, str], limit: int = 1) -> list[dict]:
    """Core questions first, then the rest in order. This is the whole adaptive
    policy: there is no value in a clever ordering when the 12 core answers are
    the ones that unlock both cards.
    """
    core = [q for q in core_questions() if not answers.get(q["text"])]
    rest = [q for q in all_questions()
            if not q["is_core"] and not answers.get(q["text"])]
    return (core + rest)[:limit]


def progress(answers: dict[str, str]) -> dict:
    qs = all_questions()
    by_part: dict[str, dict] = {}
    for p in _data()["parts"]:
        in_part = [q for q in qs if q["part"] == p["key"]]
        done = sum(1 for q in in_part if answers.get(q["text"]))
        by_part[p["key"]] = {"label": p["label"], "done": done,
                             "total": len(in_part)}
    core_done = sum(1 for t in CORE_TEXTS if answers.get(t))
    return {"answered": sum(1 for q in qs if answers.get(q["text"])),
            "total": len(qs),
            "core_done": core_done, "core_total": len(CORE_TEXTS),
            "ready_for_dialogue": core_done >= len(CORE_TEXTS),
            "by_part": by_part}
