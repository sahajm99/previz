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
import re
from dataclasses import asdict
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
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


class MoreIn(BaseModel):
    n: int = 2
    style: str = "realistic"
    instruction: str = ""


@router.post("/shots/{shot_id}/more")
def more_like(shot_id: str, body: MoreIn, story_id: str | None = None):
    """More frames off one chosen frame. Each is a fresh shot conditioned on the
    frame you liked, so it stays the same person, lighting and world (§6.2).
    """
    sid = _sid(story_id)
    try:
        sc, src = store.shot(sid, shot_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    def work(emit):
        from app.images import load_png
        seed = load_png(src.image_url) if src.image_url else None
        n = max(1, min(body.n, 4))
        note = body.instruction.strip()
        emit.thinking(f"{n} more like shot {src.number}"
                      + (f": {note}" if note else ""), agent="ShotPromptWriter")
        made = []
        for _ in range(n):
            desc = f"{src.description} ({note})" if note else src.description
            made.append(store.add_shot(
                sid, sc.id, desc, shot_size=src.shot_size, angle=src.angle,
                lens=src.lens, movement=src.movement, subject=src.subject,
                characters=list(src.characters)))
        _render_shots(emit, sid, sc, made, body.style, carry=True,
                      seed_frame=seed)
        return {"added": len(made)}

    return stream(work, agent="ShotPromptWriter")


class TinkerIn(BaseModel):
    instruction: str
    style: str = "realistic"


@router.post("/shots/{shot_id}/tinker")
def tinker_shot(shot_id: str, body: TinkerIn, story_id: str | None = None):
    """Nudge one frame with a plain instruction ("make it rain harder"). Same
    shot, regenerated conditioned on its own current frame plus the instruction.
    """
    sid = _sid(story_id)
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(400, "instruction is required")
    try:
        sc, sh = store.shot(sid, shot_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    def work(emit):
        from app.images import load_png
        seed = load_png(sh.image_url) if sh.image_url else None
        emit.thinking(f"tinkering shot {sh.number}: {instruction}",
                      agent="ShotPromptWriter")
        _render_shots(emit, sid, sc, [sh], body.style, carry=True,
                      seed_frame=seed, extra_direction=instruction)
        return {"shot_id": sh.id}

    return stream(work, agent="ShotPromptWriter")


class ChatIn(BaseModel):
    message: str


@router.post("/scenes/{number}/chat")
def scene_chat(number: int, body: ChatIn, story_id: str | None = None):
    """Talk a scene's coverage through in text, before any image is paid for.

    Grounded in the same Continuity Pack the shot planner uses, so the director's
    suggestions do not contradict the bible or leak past the knowledge horizon.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    sc = st.scene_by_number(number)
    if not sc:
        raise HTTPException(404, f"no scene {number}")
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "message is required")

    def work(emit):
        from google.genai import types

        from app.bible import build_pack
        from app.consistency import _SAFETY, _client
        from app.voice import REASONING_MODEL

        pack = build_pack(sid, query=f"{sc.slugline} {msg}",
                          character_ids=sc.characters, scene_number=number)
        emit.context(pack.report()["slots"], pack.chunk_ids, pack.dropped)
        emit.thinking("thinking about the scene", agent="Director")
        cast_names = [st.characters[c].name for c in sc.characters
                      if c in st.characters]
        resp = _client().models.generate_content(
            model=REASONING_MODEL,
            contents=(
                "You are a film director helping plan the visual coverage of a "
                "scene. Answer the filmmaker briefly and concretely: suggest "
                "shots, framing, blocking, lensing or mood, and ground every "
                "suggestion in this scene. Do not write dialogue.\n\n"
                f"{pack.text()}\n\n"
                f"SCENE {number}: {sc.slugline}\n{sc.synopsis}\n"
                + (f"\nSCENE TEXT:\n{sc.body[:1500]}" if sc.body else "")
                + f"\nCHARACTERS PRESENT: {', '.join(cast_names) or 'none'}\n\n"
                f"FILMMAKER: {msg}"),
            config=types.GenerateContentConfig(safety_settings=_SAFETY),
        )
        return {"reply": (resp.text or "").strip()}

    return stream(work, agent="Director")


def _extract_script_text(file: UploadFile | None, text: str) -> str:
    """Pasted text wins. Otherwise pull text out of the upload: PDF via pypdf,
    anything else decoded as utf-8 (a .txt or .fountain screenplay).
    """
    if text and text.strip():
        return text
    if file is None:
        return ""
    raw = file.file.read()
    name = (file.filename or "").lower()
    if name.endswith(".pdf") or raw[:5] == b"%PDF-":
        try:
            import io

            import pypdf
        except Exception as exc:                        # pypdf not installed
            raise HTTPException(
                400, "PDF import needs the pypdf package. Paste the script text "
                     "instead, or add pypdf to requirements.") from exc
        reader = pypdf.PdfReader(io.BytesIO(raw))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    return raw.decode("utf-8", errors="ignore")


_NUM_SLUG = re.compile(
    r"^\s*\d+[.)]?\s+((?:INT|EXT|EST|I/E|INT\./EXT)\b.*?)(?:\s+\d+)?\s*$", re.I)


def _normalize_script(text: str) -> str:
    """Help the heading detector find sluglines in text pulled out of a PDF,
    where they often arrive numbered ("12 INT. BUS DEPOT - NIGHT"). The detector
    needs a line to START with INT./EXT., so strip a leading scene number.
    """
    out = []
    for ln in text.splitlines():
        m = _NUM_SLUG.match(ln)
        out.append(m.group(1).strip() if m else ln)
    return "\n".join(out)


def _characters_in(lines) -> list[str]:
    """Distinct speaking-character names in a scene, in order of first appearance."""
    from app import screenplay

    seen: set[str] = set()
    out: list[str] = []
    for nm in screenplay.speaking(lines or []):
        key = (nm or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(nm.strip())
    return out


def _title_from(file, parsed) -> str:
    """A cheap title for the imported story: the file name, else the first
    location. No model call, so import stays instant and works offline.
    """
    if file is not None and file.filename:
        stem = file.filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        stem = re.sub(r"[-_]+", " ", stem).strip()
        if stem:
            return stem.title()[:80]
    first = parsed[0] if parsed else {}
    return ((first.get("location") or first.get("slugline") or "").strip()
            or "Imported Script")[:80]


def _appearances(st, cid: str) -> int:
    return sum(1 for sc in st.scenes.values() if cid in sc.characters)


def _rank_leads(st, char_ids: list[str], limit: int) -> list[str]:
    """The characters who carry the story, by how many scenes they are in."""
    present = [c for c in char_ids if _appearances(st, c) > 0]
    return sorted(present, key=lambda c: _appearances(st, c), reverse=True)[:limit]


@router.post("/scenes/import")
def import_script(file: UploadFile | None = File(None),
                  text: str = Form(""), replace: bool = Form(True),
                  story_id: str | None = None):
    """Upload a PDF or paste a screenplay and rebuild the story around it: one
    script, one story, one bible.

    Parsing and character registration are text, so this is instant and free. It
    registers every speaking character but generates no image. Casting the leads
    (their reference sheets) is a separate streamed step: POST /api/board/cast-leads.
    """
    from app import screenplay

    sid = _sid(story_id)
    st = store.story(sid)
    script_text = _extract_script_text(file, text)
    if not script_text.strip():
        raise HTTPException(400, "no script text found in the upload")

    parsed = screenplay.split_scenes(
        screenplay.parse(_normalize_script(script_text)))
    if not parsed:
        raise HTTPException(400, "could not find any scenes in the script")

    # Replace: wipe the current story so the bible, cast and scenes all belong to
    # this script, and nothing bleeds in from whatever story was here before.
    if replace:
        st.scenes.clear()
        st.characters.clear()
        st.locations.clear()
        st.proposals.clear()
        st.title = _title_from(file, parsed)
        st.logline = ""
        st.summary = ""
    start = max((s.number for s in st.scenes.values()), default=0) + 1

    # First pass: register every distinct speaking character, once.
    name_to_id: dict[str, str] = {}
    for scd in parsed:
        for nm in _characters_in(scd.get("lines", [])):
            key = nm.lower()
            if key in name_to_id:
                continue
            existing = None if replace else st.character_by_name(nm)
            c = existing or store.add_character(sid, nm.title())
            name_to_id[key] = c.id

    # Second pass: create the scenes, binding each to the characters in it.
    for i, scd in enumerate(parsed):
        number = start + i
        lines = scd.get("lines", [])
        body = screenplay.to_text(lines) if lines else ""
        # A headingless chunk (text before the first slugline) parses with an
        # empty slugline, so fall back to a numbered name or the scene shows blank.
        slug = (scd.get("slugline") or "").strip() or f"SCENE {number}"
        synopsis = (next((ln.text for ln in lines
                          if getattr(ln, "type", "") == "action"
                          and ln.text.strip()), "").strip()
                    or body.strip()[:160] or slug)[:200]
        char_ids: list[str] = []
        for nm in _characters_in(lines):
            cid = name_to_id.get(nm.lower())
            if cid and cid not in char_ids:
                char_ids.append(cid)
        store.add_scene(sid, number, slug, synopsis=synopsis, body=body,
                        int_ext=scd.get("int_ext") or "INT",
                        time_of_day=scd.get("time_of_day") or "DAY",
                        characters=char_ids)

    try:
        from app.bible import reindex_story
        reindex_story(sid)
    except Exception as exc:                            # noqa: BLE001
        print(f"  import reindex skipped: {type(exc).__name__}: {exc}")

    lead_ids = _rank_leads(st, list(name_to_id.values()), limit=2)
    return {"imported": len(parsed), "first_number": start,
            "title": st.title,
            "scenes": st.scene_index(),
            "characters": [{"id": cid, "name": st.characters[cid].name}
                           for cid in name_to_id.values() if cid in st.characters],
            "leads": [{"id": cid, "name": st.characters[cid].name}
                      for cid in lead_ids]}


def _answers_from_script(st, c) -> dict[str, str]:
    """Infer answers to the core character questions from the script itself, so a
    character pulled out of a screenplay can be cast without a manual interview.
    The answers are the same {question_text: answer} contract the interview
    produces, so the existing card compilers consume them unchanged.
    """
    import json as _json

    from google.genai import types

    from app.consistency import _SAFETY, _client
    from app.questions import core_questions
    from app.voice import REASONING_MODEL

    ctx = "\n\n".join(sc.body for sc in st.scenes.values()
                      if c.id in sc.characters and sc.body)[:6000]
    core = core_questions()
    resp = _client().models.generate_content(
        model=REASONING_MODEL,
        contents=(
            f"From this screenplay, infer who the character {c.name} is. Answer "
            f"each question in first person as {c.name}, one to three sentences, "
            f"concrete and specific. Where the script does not say, invent a "
            f"plausible, specific detail that fits it. Never hedge.\n\n"
            f"SCRIPT:\n{ctx or c.name}\n\n"
            + "\n".join(f"{i + 1}. {q['text']}" for i, q in enumerate(core))
            + f"\n\nReturn JSON {{\"answers\": [\"...\"]}} with exactly "
              f"{len(core)} strings, in order."),
        config=types.GenerateContentConfig(
            response_mime_type="application/json", safety_settings=_SAFETY,
            max_output_tokens=4096),
    )
    got = _json.loads(resp.text).get("answers", [])
    return {q["text"]: (got[i] or "").strip()
            for i, q in enumerate(core) if i < len(got) and got[i]}


class CastLeadsIn(BaseModel):
    limit: int = 2


@router.post("/board/cast-leads")
def cast_leads(body: CastLeadsIn, story_id: str | None = None):
    """Cast the story's main characters: infer each lead's look from the script,
    compile an Identity Card, and generate the reference sheet the face referee
    locks onto. This is the image cost of import, made explicit and streamed, and
    it is what makes an imported script's storyboard consistent rather than generic.
    """
    sid = _sid(story_id)
    st = store.story(sid)
    leads = [st.characters[cid]
             for cid in _rank_leads(st, list(st.characters), max(1, body.limit))]
    if not leads:
        raise HTTPException(400, "no characters to cast; import a script first")

    def work(emit):
        from app.api.cast import CARDS
        from app.bible import reindex_entity
        from app.consistency import (IdentityCard, compile_identity_card,
                                     fingerprint, generate_reference_sheet)
        from app.images import save_png

        cast_names: list[str] = []
        for c in leads:
            emit.thinking(f"casting {c.name}: reading the script for their look",
                          agent="CastingDirector")
            if not c.answers:
                c.answers = _answers_from_script(st, c)
            if not c.identity_card:
                ic = compile_identity_card(c.name, c.answers, c.canon_version)
                c.identity_card = {"descriptor": ic.descriptor,
                                   "wardrobe": ic.wardrobe,
                                   "negative": ic.negative,
                                   "canon_version": c.canon_version}
                emit.partial("identity_card", ic.descriptor)
            card = IdentityCard(name=c.name,
                                descriptor=c.identity_card["descriptor"],
                                wardrobe=c.identity_card["wardrobe"],
                                negative=c.identity_card.get("negative", ""),
                                canon_version=c.canon_version)
            emit.tool_call("generate_reference_sheet", {"character": c.name})
            _charge(sid, 1)
            card.sheet_png = generate_reference_sheet(card)
            c.sheet_url = save_png(card.sheet_png,
                                   f"sheet_{c.id}_v{c.canon_version}")
            emit.tool_result("generate_reference_sheet", f"sheet at {c.sheet_url}")
            card.face_embedding = fingerprint(card)
            if card.face_embedding:
                CARDS[c.id] = card
                emit.tool_result("fingerprint", f"{len(card.face_embedding)} dims")
            else:
                emit.violation("no_face",
                               f"no face detected on {c.name}'s sheet, so no "
                               f"fingerprint; frames generate but cannot be scored")
            reindex_entity(sid, "character", c.id)
            cast_names.append(c.name)
        return {"cast": cast_names}

    return stream(work, agent="CastingDirector")


def _render_shots(emit, sid: str, sc, shots: list, style: str,
                  carry: bool, seed_frame: bytes | None = None,
                  extra_direction: str = "") -> str | None:
    """The generation loop. Shared by the whole scene and the single shot path.

    `seed_frame` conditions the first frame on a specific image instead of the
    previous shot in sequence, which is what "generate more" and "tinker" build
    off. `extra_direction` rides along in the prompt (a tinker instruction)
    without being stored on the shot.
    """
    from app.api.cast import CARDS
    from app.consistency import IdentityCard, generate_shot_with_referee
    from app.images import load_png, save_png

    st = store.story(sid)
    previous: bytes | None = seed_frame
    if previous is None and carry:
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
            if extra_direction:
                desc += " " + extra_direction.strip()
            frame, verdicts, attempts = generate_shot_with_referee(
                f"{sh.shot_size}, {sh.angle} angle, {sh.lens}, "
                f"{sh.movement}. {desc}",
                cards, style=style, previous_frame=previous)
        except Exception as exc:                        # noqa: BLE001
            sh.status = "failed"
            emit.error(f"shot {sh.number}: {type(exc).__name__}: {exc}",
                       retryable="RESOURCE_EXHAUSTED" in str(exc))
            continue

        sh.image_url = save_png(frame, f"shot_{sh.id}_{uuid4().hex[:6]}")
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
