"""Script: the screenplay, and dialogue with the voice referee.

OWNER: Sampreeth. `app/voice.py` is the engine.

The load bearing detail is in `write_exchange`, and it was found by getting it
wrong: `scene` is shared with every character sub-agent, so it must contain
nothing any character in it does not know. Secrets go in that character's `knows`
list, never in the scene brief. A first run put "Maya already knew and did not
tell him" in the brief, and Ravi accused her of it on his third line, because he
read it there. The knowledge horizon is only as tight as the text around it.

So this endpoint builds the brief from the scene's *slugline and synopsis* and
passes per character knowledge separately, from `character.knows_by(scene)`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import screenplay
from app.bible import build_pack, reindex_entity
from app.screenplay import (action_block, dialogue_block, lines_json, parse,
                            stats, to_text)
from app.sse import stream
from app.store import scene_json, store

router = APIRouter()


def _sid(story_id: str | None) -> str:
    try:
        return store.story(story_id).id
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


class DialogueIn(BaseModel):
    turns: int = 4
    brief: str = ""          # overrides the scene synopsis if given
    character_ids: list[str] = []


@router.post("/scenes/{number}/dialogue")
def write_dialogue(number: int, body: DialogueIn, story_id: str | None = None):
    """One sub-agent per character present, each speaking from its own card.

    No single agent holds every character. That is what makes the voices
    genuinely separate rather than one model doing impressions of several people.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")

    ids = body.character_ids or sc.characters
    present = [st.characters[c] for c in ids if c in st.characters]
    if not present:
        raise HTTPException(400, "no characters in this scene")

    def work(emit):
        from app.voice import VoiceCard, compile_voice_card, embed_text, write_exchange

        cards, refused = [], []
        for c in present:
            if c.core_answered < 12:
                # Refuse rather than invent a person. §9.2.
                refused.append(c.name)
                emit.violation(
                    "incomplete_character",
                    f"{c.name} has {c.core_answered} of 12 core answers. "
                    f"Writing their dialogue would be inventing them, not "
                    f"writing them. Open the interview.")
                continue
            if not c.voice_card:
                emit.thinking(f"{c.name} has no Voice Card yet, compiling one",
                              agent="DialogueCoach")
                vc = compile_voice_card(c.name, c.answers, c.canon_version)
                c.voice_card = {"card": vc.card, "register": vc.register,
                                "phrases": vc.phrases,
                                "never_says": vc.never_says,
                                "samples": vc.samples,
                                "embedding": vc.embedding,
                                "canon_version": c.canon_version}
                reindex_entity(sid, "character", c.id)
            d = c.voice_card
            card = VoiceCard(name=c.name, card=d["card"],
                             register=d.get("register", {}),
                             phrases=d.get("phrases", []),
                             never_says=d.get("never_says", []),
                             samples=d.get("samples", []),
                             embedding=d.get("embedding"),
                             canon_version=d.get("canon_version", 1))
            if card.embedding is None and card.samples:
                # Fingerprint the samples now. Without it there is no referee, and
                # a referee that cannot score is reported, not faked.
                vecs = embed_text(card.samples)
                card.embedding = [sum(x) / len(x) for x in zip(*vecs)]
                c.voice_card["embedding"] = card.embedding
            cards.append(card)

        if not cards:
            raise RuntimeError(
                f"no character in scene {number} has the 12 core answers "
                f"needed to write dialogue: {', '.join(refused)}")

        # The brief is what a camera in the room could see. Nothing private.
        brief = body.brief or f"{sc.slugline}. {sc.synopsis}"
        by_name = {c.name: c for c in present}
        knows = {card.name: by_name[card.name].knows_by(number)
                 for card in cards if card.name in by_name}
        states = {}
        for card in cards:
            ch = by_name.get(card.name)
            cont = sc.continuity.get(ch.id) if ch else None
            if cont:
                states[card.name] = "; ".join(
                    v for v in (cont.get("physical"), cont.get("emotional"))
                    if v)

        pack = build_pack(sid, query=brief, character_ids=[c.id for c in present],
                          scene_number=number)
        emit.context(pack.report()["slots"], pack.chunk_ids, pack.dropped)
        for card in cards:
            emit.thinking(
                f"{card.name}: sub-agent built from Voice Card v"
                f"{card.canon_version}, verbatim. Knows "
                f"{len(knows.get(card.name, []))} facts as of scene {number}.",
                agent="DialogueDirector")

        lines = write_exchange(cards, brief, knows, states, turns=body.turns)
        for ln in lines:
            emit.line_ready(ln)
            if ln.get("passed") is False:
                emit.violation("voice_drift",
                               f"{ln['character']}: {ln.get('reason','')}")

        # Append to the scene body in screenplay form and reindex, so the next
        # call to this scene reads what this one wrote.
        block = "\n".join(f"{ln['character'].upper()}\n{ln['line']}\n"
                          for ln in lines if ln["line"] != "[says nothing]")
        if block:
            sc.body = (sc.body + "\n\n" + block).strip()
            sc.status = "written"
            reindex_entity(sid, "scene", sc.id)
        return {"lines": lines, "scene": scene_json(sc), "refused": refused}

    return stream(work, agent="DialogueDirector")


class ActionIn(BaseModel):
    intent: str = ""


@router.post("/scenes/{number}/action")
def write_action(number: int, body: ActionIn, story_id: str | None = None):
    """Exactly one action paragraph. Enforced by response schema, not by asking.

    That is the whole mechanism behind "no slop": a model given a
    `{"action": string}` schema cannot return three paragraphs and a scene
    heading, however much it would like to.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")

    def work(emit):
        from google.genai import types

        from app.consistency import _SAFETY, _client
        from app.voice import REASONING_MODEL

        pack = build_pack(sid, query=body.intent or sc.synopsis,
                          character_ids=sc.characters, scene_number=number)
        emit.context(pack.report()["slots"], pack.chunk_ids, pack.dropped)
        emit.thinking("one action paragraph, present tense, only what a camera "
                      "sees", agent="ActionWriter")
        resp = _client().models.generate_content(
            model=REASONING_MODEL,
            contents=(
                "You are writing screenplay action. Present tense. Only what a "
                "camera could see or a microphone could hear. No interiority, no "
                "adverbs doing the work a verb should do, no camera directions.\n\n"
                f"{pack.text()}\n\n"
                f"SCENE {number}: {sc.slugline}\n{sc.synopsis}\n"
                + (f"\nSCENE SO FAR:\n{sc.body[-1200:]}" if sc.body else "")
                + (f"\n\nWHAT THIS PARAGRAPH MUST DO: {body.intent}"
                   if body.intent else "")
                + "\n\nReturn JSON {\"action\": \"...\"} with EXACTLY ONE "
                  "paragraph, at most four lines."),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema={"type": "object",
                                 "properties": {"action": {"type": "string"}},
                                 "required": ["action"]},
                safety_settings=_SAFETY),
        )
        import json as _json
        action = _json.loads(resp.text)["action"].strip()
        emit.partial("action", action)
        sc.body = (sc.body + "\n\n" + action).strip()
        sc.status = "written"
        reindex_entity(sid, "scene", sc.id)
        return {"action": action, "scene": scene_json(sc)}

    return stream(work, agent="ActionWriter")


@router.post("/scenes/{number}/supervise")
def supervise(number: int, story_id: str | None = None):
    """ScriptSupervisor: check what is written against canon and the horizon.

    Findings come back as proposals and violations, never as edits. The user
    decides. §5.1.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")
    if not sc.body.strip():
        raise HTTPException(400, "nothing written in this scene yet")

    def work(emit):
        import json as _json

        from google.genai import types

        from app.consistency import _SAFETY, _client
        from app.voice import REASONING_MODEL

        horizon = {st.characters[c].name: st.characters[c].knows_by(number)
                   for c in sc.characters if c in st.characters}
        pack = build_pack(sid, query=sc.synopsis,
                          character_ids=sc.characters, scene_number=number)
        emit.context(pack.report()["slots"], pack.chunk_ids, pack.dropped)
        emit.thinking("checking for knowledge violations, prop and wardrobe "
                      "contradictions, and time of day mismatches",
                      agent="ScriptSupervisor")
        resp = _client().models.generate_content(
            model=REASONING_MODEL,
            contents=(
                "You are a script supervisor. Find continuity errors in this "
                "scene against established canon. Be specific and be strict, but "
                "do not invent problems: an empty list is a valid answer.\n\n"
                f"{pack.text()}\n\n"
                f"WHAT EACH CHARACTER KNOWS AS OF SCENE {number}:\n"
                f"{_json.dumps(horizon, indent=1)}\n\n"
                f"SCENE {number} AS WRITTEN:\n{sc.body[:4000]}\n\n"
                'Return JSON {"findings": [{"kind": '
                '"knowledge|prop|wardrobe|time|voice", "detail": "...", '
                '"quote": "the exact text at fault"}]}'),
            config=types.GenerateContentConfig(
                response_mime_type="application/json", safety_settings=_SAFETY),
        )
        findings = _json.loads(resp.text).get("findings", [])
        for f in findings:
            emit.violation(f.get("kind", "unknown"), f.get("detail", ""))
            p = store.propose(sid, "scene", sc.id, "continuity",
                              f.get("quote", ""), f.get("detail", ""),
                              "ScriptSupervisor")
            emit.proposal(p.id, "continuity", f.get("detail", ""))
        return {"findings": findings, "clean": not findings}

    return stream(work, agent="ScriptSupervisor")


# ===================================================================== the canvas
#
# Everything below serves the screenplay editor. Two rules shape all of it.
#
# One: the editor stores typed elements, never styled text. `app/screenplay.py`
# owns the element grammar and the margins, and it is served to the browser at
# /screenplay/grammar rather than duplicated in JavaScript, so the page on screen
# and the page on disk cannot disagree about what a character cue is.
#
# Two: the AI writes ONE element at a time. Not a scene, not a page. A writer
# accepts or discards a single line and stays the author of the sequence, and an
# agent that returns a whole scene is an agent whose output nobody reads closely
# enough to catch a wrong one.


@router.get("/screenplay/grammar")
def screenplay_grammar():
    """The element table, the margins, the Enter and Tab rules.

    Fetched once by the editor on load. This is the single definition of screenplay
    format in the product.
    """
    return screenplay.grammar()


def _cast_of(st, sc) -> list[dict]:
    """Who is in this scene, and whether their voice agent can actually run.

    `ready` is the gate from §9.2: under 12 core answers we refuse to write a
    character rather than invent one, so the panel shows the button disabled with
    the count instead of failing after the click.
    """
    out = []
    for cid in sc.characters:
        c = st.characters.get(cid)
        if not c:
            continue
        out.append({"id": c.id, "name": c.name, "role": c.role,
                    "core_answered": c.core_answered,
                    "has_voice_card": c.voice_card is not None,
                    "canon_version": c.canon_version,
                    "knows": c.knows_by(sc.number),
                    "ready": c.core_answered >= 12})
    return out


@router.get("/scenes/{number}/screenplay")
def get_screenplay(number: int, story_id: str | None = None):
    """The scene as typed elements, with its page count and its cast."""
    sid = _sid(story_id)
    st = store.story(sid)
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")
    lines = parse(sc.body)
    return {"number": sc.number, "slugline": sc.slugline,
            "synopsis": sc.synopsis, "status": sc.status,
            "lines": lines_json(lines), "stats": stats(lines),
            "cast": _cast_of(st, sc)}


class ScriptIn(BaseModel):
    text: str


@router.put("/scenes/{number}/screenplay")
def put_screenplay(number: int, body: ScriptIn, story_id: str | None = None):
    """Save the canvas. Parse, normalise, reindex, all in this one request.

    The reindex is not deferred, and that is invariant one from the spec §4.1:
    there is no window where the scene text and the retrieval index disagree. It
    is also what makes the knowledge base build itself while you type, because the
    next agent run retrieves from the words you wrote a second ago rather than
    from whatever was last imported.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")
    lines = parse(body.text)
    # Store the normalised form, so what comes back on the next GET is exactly
    # what the parser understood, not what was typed.
    sc.body = to_text(lines)
    if lines:
        sc.status = "written"
    # A heading typed into the canvas renames the scene. The slugline is canon and
    # the editor is allowed to write it, because the writer typing INT. ELSEWHERE
    # is a decision, not a suggestion.
    head = next((l for l in lines if l.type == "scene_heading"), None)
    if head and head.text != sc.slugline:
        sc.slugline = head.text
        ie, _loc, tod = screenplay.parse_slugline(head.text)
        sc.int_ext = ie or sc.int_ext
        sc.time_of_day = tod or sc.time_of_day
    reindex_entity(sid, "scene", sc.id)
    return {"lines": lines_json(lines), "stats": stats(lines),
            "slugline": sc.slugline, "saved": True}


def _card_for(ch):
    """A VoiceCard for a stored character, compiling and fingerprinting if needed.

    Deliberately a separate path from write_dialogue's inline version rather than a
    refactor of it, because write_dialogue is working and demoed and today is not
    the day to touch it. Both read and write `character.voice_card`, so they cannot
    drift: the card is stored once and reused verbatim, which is the whole
    consistency mechanism.
    """
    from app.voice import VoiceCard, compile_voice_card, embed_text

    if not ch.voice_card:
        vc = compile_voice_card(ch.name, ch.answers, ch.canon_version)
        ch.voice_card = {"card": vc.card, "register": vc.register,
                         "phrases": vc.phrases, "never_says": vc.never_says,
                         "samples": vc.samples, "embedding": vc.embedding,
                         "canon_version": ch.canon_version}
    d = ch.voice_card
    card = VoiceCard(name=ch.name, card=d["card"], register=d.get("register", {}),
                     phrases=d.get("phrases", []),
                     never_says=d.get("never_says", []),
                     samples=d.get("samples", []), embedding=d.get("embedding"),
                     canon_version=d.get("canon_version", 1))
    if card.embedding is None and card.samples:
        vecs = embed_text(card.samples)
        card.embedding = [sum(x) / len(x) for x in zip(*vecs)]
        ch.voice_card["embedding"] = card.embedding
    return card


def _heard_from(text: str) -> list[str]:
    """What has actually been said out loud on the page, in order.

    Read off the typed elements rather than tracked separately, so the agent hears
    the page as it currently stands including lines the writer typed themselves or
    edited after accepting them.
    """
    heard: list[str] = []
    who = ""
    for l in parse(text):
        if l.type == "character":
            who = l.text.upper()
        elif l.type == "dialogue" and who:
            heard.append(f"{who}: {l.text}")
    return heard


class LineIn(BaseModel):
    character_id: str
    parenthetical: str = ""
    brief: str = ""          # overrides the scene synopsis if given
    on_page: str = ""        # the canvas as it stands, unsaved edits included


@router.post("/scenes/{number}/next-line")
def next_line(number: int, body: LineIn, story_id: str | None = None):
    """ONE line, from ONE character's own sub-agent, scored.

    This is the editor's dialogue button. It differs from /dialogue in three ways
    that all matter for writing rather than demoing: one line instead of an
    exchange, the character is chosen by the writer rather than alternated, and
    nothing is written to canon. The line comes back as typed elements and the
    writer decides where it goes.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")
    ch = st.characters.get(body.character_id)
    if not ch:
        raise HTTPException(404, "no such character")
    if ch.id not in sc.characters:
        raise HTTPException(400, f"{ch.name} is not in scene {number}")

    def work(emit):
        from app.voice import referee_line, speak
        from app.voice import _check_scene_for_secrets  # noqa: PLC2701

        if ch.core_answered < 12:
            # Refuse rather than invent a person. §9.2.
            emit.violation(
                "incomplete_character",
                f"{ch.name} has {ch.core_answered} of 12 core answers. Writing "
                f"their dialogue would be inventing them, not writing them.")
            raise RuntimeError(f"{ch.name} is not ready for dialogue")

        if not ch.voice_card:
            emit.thinking(f"{ch.name} has no Voice Card yet, compiling one from "
                          f"their interview answers", agent="DialogueCoach")
        card = _card_for(ch)
        reindex_entity(sid, "character", ch.id)

        # The brief is what a camera in the room could see, and nothing else. This
        # is the rule that was found by breaking it: a first run put "Maya already
        # knew and did not tell him" in the shared brief and Ravi accused her of it
        # on his third line, because he read it there. Private facts go in `knows`.
        brief = body.brief or f"{sc.slugline}. {sc.synopsis}"
        knows = {c.name: st.characters[c.id].knows_by(number)
                 for c in [st.characters[i] for i in sc.characters
                           if i in st.characters]}
        leaked = _check_scene_for_secrets(brief, knows)
        for msg in leaked:
            # Loud rather than silent. A leaked secret produces dialogue that looks
            # correct and is wrong, which is the most expensive bug on this stage.
            emit.violation("knowledge_leak", msg)

        mine = knows.get(ch.name, [])
        cont = sc.continuity.get(ch.id) or {}
        state = "; ".join(v for v in (cont.get("physical"),
                                      cont.get("emotional")) if v)

        pack = build_pack(sid, query=brief, character_ids=[ch.id],
                          scene_number=number)
        emit.context(pack.report()["slots"], pack.chunk_ids, pack.dropped)

        agent_name = f"{ch.name} · Voice Card v{card.canon_version}"
        emit.thinking(
            f"sub-agent built from {ch.name}'s Voice Card verbatim, never "
            f"summarised. Knows {len(mine)} facts as of scene {number}, and "
            f"cannot refer to anything else.", agent=agent_name)

        heard = _heard_from(body.on_page or sc.body)
        line = speak(card, brief, mine, state, heard)
        v = referee_line(line, card)
        attempts = 1
        if not v.passed:
            emit.violation("voice_drift",
                           f"{ch.name}: {v.reason}. Sending it back to their own "
                           f"sub-agent with the register named.")
            line = speak(card, brief, mine, state,
                         heard + [f"(That last attempt did not sound like you. "
                                  f"Your register is: {card.register}. Try again, "
                                  f"more like your sample lines.)"])
            v = referee_line(line, card)
            attempts = 2

        elements = dialogue_block(ch.name, line, body.parenthetical)
        payload = {"character": ch.name, "character_id": ch.id, "line": line,
                   "score": v.score, "passed": v.passed, "reason": v.reason,
                   "canon_version": card.canon_version, "agent": agent_name,
                   "attempts": attempts, "elements": lines_json(elements)}
        emit.line_ready(payload)
        return {"line": payload, "heard": len(heard),
                "silent": line.strip() == "[says nothing]"}

    return stream(work, agent="DialogueDirector")


class ActionLineIn(BaseModel):
    intent: str = ""
    on_page: str = ""


@router.post("/scenes/{number}/next-action")
def next_action(number: int, body: ActionLineIn, story_id: str | None = None):
    """ONE action paragraph, as a typed element, written to nothing.

    The one paragraph limit is enforced by the response schema rather than by
    asking politely. A model handed {"action": string} cannot return three
    paragraphs and a scene heading however much it would like to, and that is the
    entire mechanism behind an AI panel that does not produce slop.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")

    def work(emit):
        import json as _json

        from google.genai import types

        from app.consistency import _SAFETY, _client
        from app.voice import REASONING_MODEL

        pack = build_pack(sid, query=body.intent or sc.synopsis,
                          character_ids=sc.characters, scene_number=number)
        emit.context(pack.report()["slots"], pack.chunk_ids, pack.dropped)
        emit.thinking("one paragraph, present tense, only what a camera sees",
                      agent="ActionWriter")
        page = (body.on_page or sc.body)[-1200:]
        resp = _client().models.generate_content(
            model=REASONING_MODEL,
            contents=(
                "You are writing screenplay action. Present tense. Only what a "
                "camera could see or a microphone could hear. No interiority, no "
                "adverbs doing the work a verb should do, no camera directions.\n\n"
                f"{pack.text()}\n\n"
                f"SCENE {number}: {sc.slugline}\n{sc.synopsis}\n"
                + (f"\nTHE PAGE SO FAR:\n{page}" if page else "")
                + (f"\n\nWHAT THIS PARAGRAPH MUST DO: {body.intent}"
                   if body.intent else "")
                + "\n\nReturn JSON {\"action\": \"...\"} with EXACTLY ONE "
                  "paragraph, at most four lines."),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema={"type": "object",
                                 "properties": {"action": {"type": "string"}},
                                 "required": ["action"]},
                safety_settings=_SAFETY),
        )
        text = _json.loads(resp.text)["action"].strip()
        emit.partial("action", text)
        return {"action": text, "agent": "ActionWriter",
                "elements": lines_json(action_block(text))}

    return stream(work, agent="ActionWriter")
