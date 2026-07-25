"""Cast: the 100 question interview and the two compiled cards.

OWNER: kk. This file is the seam, not the feature. What lands here is the flat
`{question_text: answer}` dict from the interview UI, and everything downstream
is derived from it, so this tab unblocks the board and the script without either
of them waiting on interview design decisions.

The endpoints below are deliberately dumb about interview *policy* (what to ask
next, how to phrase a follow-up). That belongs in the builder. What this file
guarantees is the contract: answers in, cards out, cards cached until canon
changes.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import questions as Q
from app.bible import reindex_entity
from app.sse import stream
from app.store import character_json, store

router = APIRouter()


def _sid(story_id: str | None) -> str:
    try:
        return store.story(story_id).id
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/questions")
def get_questions(part: str | None = None, core_only: bool = False):
    """The 100 questions. `core_only` returns the 12 that gate dialogue."""
    if core_only:
        return {"questions": Q.core_questions(), "count": 12}
    if part:
        return {"questions": [q for q in Q.all_questions() if q["part"] == part]}
    return {"count": len(Q.all_questions()), "parts": Q.parts()}


@router.get("/characters")
def list_characters(story_id: str | None = None):
    st = store.story(_sid(story_id))
    return {"characters": [character_json(c) for c in st.characters.values()]}


@router.get("/characters/{cid}")
def get_character(cid: str, story_id: str | None = None):
    st = store.story(_sid(story_id))
    c = st.characters.get(cid)
    if not c:
        raise HTTPException(404, "no such character")
    d = character_json(c)
    d["progress"] = Q.progress(c.answers)
    d["next_questions"] = Q.next_unanswered(c.answers, limit=5)
    return d


class CharacterIn(BaseModel):
    name: str
    role: str = "supporting"
    answers: dict[str, str] = {}


@router.post("/characters")
def create_character(body: CharacterIn, story_id: str | None = None):
    sid = _sid(story_id)
    c = store.add_character(sid, body.name, body.role, body.answers)
    reindex_entity(sid, "character", c.id)
    return character_json(c)


class AnswersIn(BaseModel):
    answers: dict[str, str]


@router.put("/characters/{cid}/answers")
def put_answers(cid: str, body: AnswersIn, story_id: str | None = None):
    """Merge answers. Any real change bumps canon_version and stales both cards.

    That staling is invariant two from the spec (§4.1) and it is the point: a
    corrected fact that did not force a recompile would keep producing the old
    face and the old voice forever, so the user would fix something and see
    nothing change.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    if cid not in st.characters:
        raise HTTPException(404, "no such character")
    c = store.set_answers(sid, cid, body.answers)
    reindex_entity(sid, "character", cid)
    d = character_json(c)
    d["progress"] = Q.progress(c.answers)
    return d


@router.get("/characters/{cid}/progress")
def progress(cid: str, story_id: str | None = None):
    st = store.story(_sid(story_id))
    c = st.characters.get(cid)
    if not c:
        raise HTTPException(404, "no such character")
    return Q.progress(c.answers)


@router.post("/characters/{cid}/compile")
def compile_cards(cid: str, story_id: str | None = None, force: bool = False,
                  what: str = "both"):
    """Compile the Identity Card and the Voice Card from the answers.

    Cached on the character and reused verbatim. `force` recompiles, which is the
    one deliberate way to get a new card without changing an answer.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    c = st.characters.get(cid)
    if not c:
        raise HTTPException(404, "no such character")
    if not c.answers:
        raise HTTPException(400, "no answers yet: nothing to compile from")

    def work(emit):
        did = []
        if what in ("both", "identity") and (force or not c.identity_card):
            emit.thinking(f"compiling {c.name}'s Identity Card from "
                          f"{len(c.answers)} answers", agent="CastingDirector")
            from app.consistency import compile_identity_card
            ic = compile_identity_card(c.name, c.answers, c.canon_version)
            c.identity_card = {"descriptor": ic.descriptor,
                               "wardrobe": ic.wardrobe,
                               "negative": ic.negative,
                               "canon_version": c.canon_version}
            emit.partial("identity_card", ic.descriptor)
            did.append("identity")
        if what in ("both", "voice") and (force or not c.voice_card):
            if c.core_answered < 12:
                emit.violation("incomplete_character",
                               f"{c.name} has {c.core_answered} of 12 core "
                               f"answers. A Voice Card compiled from less than "
                               f"that invents a person rather than describing "
                               f"one.")
            else:
                emit.thinking(f"compiling {c.name}'s Voice Card",
                              agent="DialogueCoach")
                from app.voice import compile_voice_card
                vc = compile_voice_card(c.name, c.answers, c.canon_version)
                c.voice_card = {"card": vc.card, "register": vc.register,
                                "phrases": vc.phrases,
                                "never_says": vc.never_says,
                                "samples": vc.samples,
                                "embedding": vc.embedding,
                                "canon_version": c.canon_version}
                emit.partial("voice_card", vc.card)
                did.append("voice")
        reindex_entity(sid, "character", cid)
        return {"character": character_json(c), "compiled": did}

    return stream(work, agent="CastingDirector")


@router.post("/characters/{cid}/cast")
def cast_character(cid: str, story_id: str | None = None):
    """Generate the reference sheet and the face fingerprint. §6.2 steps 2 and 3.

    OWNER: Sahaj. The sheet is the identity of record: every later shot is
    conditioned on this exact image, so it is generated once and never
    regenerated casually.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    c = st.characters.get(cid)
    if not c:
        raise HTTPException(404, "no such character")

    def work(emit):
        from app.consistency import (IdentityCard, compile_identity_card,
                                     fingerprint, generate_reference_sheet)
        from app.images import save_png

        if not c.identity_card:
            emit.thinking(f"no Identity Card yet, compiling one for {c.name}",
                          agent="CastingDirector")
            ic = compile_identity_card(c.name, c.answers, c.canon_version)
            c.identity_card = {"descriptor": ic.descriptor,
                               "wardrobe": ic.wardrobe,
                               "negative": ic.negative,
                               "canon_version": c.canon_version}
        card = IdentityCard(name=c.name,
                            descriptor=c.identity_card["descriptor"],
                            wardrobe=c.identity_card["wardrobe"],
                            negative=c.identity_card.get("negative", ""),
                            canon_version=c.canon_version)

        emit.tool_call("generate_reference_sheet", {"character": c.name})
        card.sheet_png = generate_reference_sheet(card)
        c.sheet_url = save_png(card.sheet_png, f"sheet_{cid}_v{c.canon_version}")
        emit.tool_result("generate_reference_sheet", f"sheet at {c.sheet_url}")

        emit.thinking("cropping the face and embedding it: this becomes the "
                      "anchor every generated frame is measured against",
                      agent="ContinuityReferee")
        card.face_embedding = fingerprint(card)
        if card.face_embedding:
            CARDS[cid] = card
            emit.tool_result("fingerprint",
                             f"{len(card.face_embedding)} dims")
        else:
            emit.violation("no_face",
                           "no face detected on the sheet, so no fingerprint. "
                           "Frames will generate but cannot be refereed.")
        reindex_entity(sid, "character", cid)
        return {"character": character_json(c),
                "fingerprinted": card.face_embedding is not None}

    return stream(work, agent="CastingDirector")


# Runtime IdentityCards, keyed by character id. Holds the sheet bytes and the face
# embedding, which are too large to serialise into the store on every read.
# Populated by /cast and read by the board.
CARDS: dict = {}
