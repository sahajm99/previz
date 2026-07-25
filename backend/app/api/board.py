"""Board: the shot list, then the frames, then the referee.

OWNER: Sahaj. `app/consistency.py` is the engine; this file is the HTTP surface
over it, and it exists so the storyboard work does not also have to be web work.

The order here is the point (§9.3): all the deciding happens in text, before any
image is paid for. Text iteration is instant and free, image iteration is neither.
A shot list is cheap to argue with; a board of six wrong frames is thirty seconds
each plus real money.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.sse import stream
from app.store import store

router = APIRouter()

MAX_PER_STORY = settings.max_images_per_story
_spent: dict[str, int] = {}


def _sid(story_id: str | None) -> str:
    try:
        return store.story(story_id).id
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def _charge(sid: str, n: int = 1) -> None:
    """Hard server side cap, §13 item 10. A retry loop must not be able to spend
    the budget, and the only way to guarantee that is to count here rather than
    trusting every call site to behave.
    """
    used = _spent.get(sid, 0)
    if used + n > MAX_PER_STORY:
        raise RuntimeError(
            f"image cap reached for this story: {used}/{MAX_PER_STORY}. "
            f"Raise MAX_IMAGES_PER_STORY only if you mean to.")
    _spent[sid] = used + n


@router.get("/board/budget")
def budget(story_id: str | None = None):
    sid = _sid(story_id)
    return {"spent": _spent.get(sid, 0), "cap": MAX_PER_STORY}


@router.get("/scenes/{number}/shots")
def get_shots(number: int, story_id: str | None = None):
    st = store.story(_sid(story_id))
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")
    return {"scene": number, "slugline": sc.slugline,
            "shots": [asdict(s) for s in sc.shots]}


class ShotListIn(BaseModel):
    count: int = 6
    note: str = ""


@router.post("/scenes/{number}/shots")
def plan_shots(number: int, body: ShotListIn, story_id: str | None = None):
    """Cinematographer: a structured, editable shot list. No images yet."""
    sid = _sid(story_id)
    st = store.story(sid)
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")

    def work(emit):
        from google.genai import types

        from app.bible import build_pack
        from app.consistency import _SAFETY, _client
        from app.voice import REASONING_MODEL

        pack = build_pack(sid, query=f"{sc.slugline} {sc.synopsis}",
                          character_ids=sc.characters, scene_number=number)
        emit.context(pack.report()["slots"], pack.chunk_ids, pack.dropped)
        emit.thinking(f"breaking scene {number} into {body.count} shots",
                      agent="Cinematographer")

        cast_names = [st.characters[c].name for c in sc.characters
                      if c in st.characters]
        resp = _client().models.generate_content(
            model=REASONING_MODEL,
            contents=(
                "You are a cinematographer breaking a scene into a shot list for "
                "a storyboard. Coverage that a director could actually shoot: "
                "vary the size, do not repeat the same framing twice in a row, "
                "and let the shot sizes carry the scene's emotional shape.\n\n"
                f"{pack.text()}\n\n"
                f"SCENE {number}: {sc.slugline}\n{sc.synopsis}\n"
                + (f"\nSCENE TEXT:\n{sc.body[:1500]}" if sc.body else "")
                + f"\n\nCHARACTERS PRESENT: {', '.join(cast_names) or 'none'}\n"
                + (f"\nDIRECTION: {body.note}\n" if body.note else "")
                + f"\nReturn JSON: {{\"shots\": [{{"
                  '"description": "what is in frame, one sentence, visual only", '
                  '"shot_size": "ECU|CU|MCU|MS|MWS|WS|EWS", '
                  '"angle": "eye|low|high|dutch|OTS|POV", '
                  '"lens": "18mm|35mm|50mm|85mm", '
                  '"movement": "static|pan|tilt|dolly|handheld|crane", '
                  '"subject": "who or what the shot is about"'
                  f"}}]}} with exactly {body.count} shots."),
            config=types.GenerateContentConfig(
                response_mime_type="application/json", safety_settings=_SAFETY),
        )
        planned = json.loads(resp.text).get("shots", [])
        sc.shots.clear()
        for spec in planned[:body.count]:
            who = [c for c in sc.characters
                   if st.characters[c].name.split()[0].lower()
                   in (spec.get("subject", "") + spec.get("description", "")).lower()]
            sh = store.add_shot(
                sid, sc.id, spec.get("description", ""),
                shot_size=spec.get("shot_size", "MS"),
                angle=spec.get("angle", "eye"), lens=spec.get("lens", "35mm"),
                movement=spec.get("movement", "static"),
                subject=spec.get("subject", ""),
                characters=who or sc.characters)
            emit.partial("shot", f"{sh.number}. {sh.shot_size} {sh.description}")
        return {"shots": [asdict(s) for s in sc.shots]}

    return stream(work, agent="Cinematographer")


class RenderIn(BaseModel):
    style: str = "realistic"
    carry_previous: bool = True


@router.post("/scenes/{number}/render")
def render_scene(number: int, body: RenderIn, story_id: str | None = None):
    """Render every planned shot in the scene, streaming each frame as it lands.

    Sequential rather than parallel, because each frame is conditioned on the
    previous approved one so lighting and blocking carry forward. That coupling
    is worth more than the wall clock: frames stream in either way, and the user
    sees the first one in about thirty seconds.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")
    if not sc.shots:
        raise HTTPException(400, "no shots planned for this scene yet")

    def work(emit):
        prev = _render_shots(emit, sid, sc, sc.shots, body.style,
                             body.carry_previous)
        return {"rendered": len(sc.shots), "last": prev}

    return stream(work, agent="ShotPromptWriter")


@router.post("/shots/{shot_id}/render")
def render_one(shot_id: str, body: RenderIn, story_id: str | None = None):
    """Regenerate one shot. The deliberate live generation in the demo."""
    sid = _sid(story_id)
    try:
        sc, sh = store.shot(sid, shot_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    def work(emit):
        _render_shots(emit, sid, sc, [sh], body.style, body.carry_previous)
        return {"shot": asdict(sh)}

    return stream(work, agent="ShotPromptWriter")


def _render_shots(emit, sid: str, sc, shots: list, style: str,
                  carry: bool) -> str | None:
    """The generation loop. Shared by the whole scene and the single shot path."""
    from app.api.cast import CARDS
    from app.consistency import IdentityCard, generate_shot_with_referee
    from app.images import load_png, save_png

    st = store.story(sid)
    previous: bytes | None = None
    if carry:
        earlier = [s for s in sc.shots if s.image_url
                   and s.number < min(x.number for x in shots)]
        if earlier:
            previous = load_png(earlier[-1].image_url)

    for sh in shots:
        cards: list[IdentityCard] = []
        for cid in sh.characters:
            c = st.characters.get(cid)
            if not c or not c.identity_card:
                continue
            card = CARDS.get(cid)
            if card is None:
                # No sheet has been generated for this character yet, so the card
                # is text only. The frame still gets the descriptor verbatim; it
                # just cannot be refereed. Reported rather than silently skipped.
                card = IdentityCard(
                    name=c.name, descriptor=c.identity_card["descriptor"],
                    wardrobe=c.identity_card["wardrobe"],
                    negative=c.identity_card.get("negative", ""),
                    canon_version=c.canon_version)
                sheet = load_png(c.sheet_url) if c.sheet_url else None
                if sheet:
                    card.sheet_png = sheet
                else:
                    emit.violation(
                        "no_reference_sheet",
                        f"{c.name} has no reference sheet, so this frame is "
                        f"conditioned on text only and cannot be scored. "
                        f"Cast them first.")
                CARDS[cid] = card
            # Scene level continuity overrides the card default (§6.2 step 4).
            cont = sc.continuity.get(cid) or {}
            if cont.get("wardrobe"):
                card = IdentityCard(**{**asdict_card(card),
                                       "wardrobe": cont["wardrobe"]})
            cards.append(card)

        sh.status = "generating"
        emit.thinking(f"shot {sh.number}: {sh.shot_size} {sh.angle}, "
                      f"{sh.lens}, {sh.movement}", agent="ShotPromptWriter")
        emit.tool_call("nano_banana", {"shot": sh.number,
                                       "characters": [c.name for c in cards]})
        try:
            _charge(sid, 1)
            desc = sh.description
            cont_notes = [f"{st.characters[cid].name}: {v.get('physical','')}"
                          for cid, v in sc.continuity.items()
                          if cid in sh.characters and v.get("physical")]
            if cont_notes:
                desc += " Continuity: " + "; ".join(cont_notes) + "."
            frame, verdicts, attempts = generate_shot_with_referee(
                f"{sh.shot_size}, {sh.angle} angle, {sh.lens}, "
                f"{sh.movement}. {desc}",
                cards, style=style, previous_frame=previous)
        except Exception as exc:                        # noqa: BLE001
            sh.status = "failed"
            emit.error(f"shot {sh.number}: {type(exc).__name__}: {exc}",
                       retryable="RESOURCE_EXHAUSTED" in str(exc))
            continue

        sh.image_url = save_png(frame, f"shot_{sh.id}_v{sh.number}")
        sh.attempts = attempts
        sh.style_preset = style
        name_to_id = {st.characters[c].name: c for c in sh.characters
                      if c in st.characters}
        sh.face_scores = {name_to_id.get(n, n): v.score
                          for n, v in verdicts.items()}
        passed = all(v.passed for v in verdicts.values())
        sh.status = "ready" if passed else "flagged"
        for n, v in verdicts.items():
            if not v.passed:
                emit.violation("face_drift",
                               f"shot {sh.number}, {n}: {v.reason}", attempts)
        emit.tool_result("nano_banana",
                         f"shot {sh.number} in {attempts} attempt(s)")
        emit.shot_ready(asdict(sh))
        previous = frame
    return previous and "carried"


def asdict_card(card) -> dict:
    return {"name": card.name, "descriptor": card.descriptor,
            "wardrobe": card.wardrobe, "negative": card.negative,
            "sheet_png": card.sheet_png,
            "face_embedding": card.face_embedding,
            "canon_version": card.canon_version}
