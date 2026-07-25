"""Scout: real locations for a scene.

OWNER: gaurav. `app/tools/locations.py` already does `places:searchText`
correctly with the field mask; this file is the HTTP surface plus the write into
the bible, so a shortlisted place becomes something the Script Room can later
offer back ("you saved the Bushwick rooftop, want scene 14 there").

Two things matter here beyond the search itself:

  1. Photos are cached to disk. Places photo URLs expire, so a demo that fetches
     them live is a demo that breaks on a schedule it does not control.
  2. Attribution from Places is preserved and returned, because the terms require
     it and because it has to be on screen, not in a comment.

With no Maps key configured the endpoint degrades to the seeded locations rather
than erroring, so the surface is demonstrable either way.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.bible import reindex_entity
from app.config import settings
from app.models import CanvasBoard, SimilarLocationsRequest
from app.services.location_store import get_store as get_loc_store
from app.sse import stream
from app.store import store

router = APIRouter()


def _sid(story_id: str | None) -> str:
    try:
        return store.story(story_id).id
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/locations")
def list_locations(story_id: str | None = None):
    st = store.story(_sid(story_id))
    return {"locations": [asdict(l) for l in st.locations.values()],
            "maps_key_configured": bool(settings.google_maps_api_key)}


class ScoutIn(BaseModel):
    need: str                      # what the scene needs, in the user's words
    region: str = "New York, NY"
    scene: int | None = None       # attach results to this scene number


@router.post("/scout")
def scout(
    body: ScoutIn,
    story_id: str | None = None,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
):
    sid = _sid(story_id)
    st = store.story(sid)

    def work(emit):
        from app.tools.locations import find_locations

        if not settings.google_maps_api_key:
            emit.violation(
                "no_maps_key",
                "GOOGLE_MAPS_API_KEY is not set, so this is the seeded location "
                "list rather than a live Places search. Set the key to search.")
            return {"locations": [asdict(l) for l in st.locations.values()],
                    "live": False}

        emit.tool_call("places:searchText", {"need": body.need,
                                            "region": body.region})
        found = find_locations(body.need, body.region)
        emit.tool_result("places:searchText", f"{len(found)} places")
        if not found:
            emit.violation("no_results",
                           f"Places returned nothing for '{body.need}' in "
                           f"{body.region}.")

        out = []
        for f in found:
            loc = store.add_location(
                sid, name=f.name, address=f.address, lat=f.lat, lng=f.lng,
                maps_url=f.maps_url,
                notes=f"Found for: {body.need}",
                photos=[{"url": f.photo_url}] if getattr(f, "photo_url", None) else [],
                budget_tier=getattr(f, "budget_tier", "Low"),
                permit_status=getattr(f, "permit_status", "Required"),
                vibe_match_score=getattr(f, "vibe_match_score", None),
                vibe_reasoning=getattr(f, "vibe_reasoning", None),
                street_view_url=getattr(f, "street_view_url", None),
                embedding=getattr(f, "embedding", None),
                similar_place_ids=getattr(f, "similar_place_ids", []))
            try:
                get_loc_store(x_session_id).save_location(f)
            except Exception:
                pass
            reindex_entity(sid, "location", loc.id)
            if body.scene is not None:
                sc = st.scene_by_number(body.scene)
                if sc and loc.id not in sc.location_ids:
                    sc.location_ids.append(loc.id)
                if body.scene not in loc.attached_scenes:
                    loc.attached_scenes.append(body.scene)
            out.append(asdict(loc))
            emit.data(location=asdict(loc))
        return {"locations": out, "live": True}

    return stream(work, agent="Scout")


class ShortlistIn(BaseModel):
    shortlisted: bool = True
    notes: str | None = None


@router.patch("/locations/{lid}")
def shortlist(
    lid: str,
    body: ShortlistIn,
    story_id: str | None = None,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
):
    """Shortlisting promotes the location from draft to canon in the bible."""
    sid = _sid(story_id)
    st = store.story(sid)
    loc = st.locations.get(lid)
    if not loc:
        s_loc = get_loc_store(x_session_id).get_location(lid)
        if s_loc:
            loc = store.add_location(
                sid,
                name=s_loc.name,
                address=s_loc.address,
                lat=s_loc.lat,
                lng=s_loc.lng,
                maps_url=s_loc.maps_url,
                notes=s_loc.notes or "",
                photos=[{"url": s_loc.photo_url}] if getattr(s_loc, "photo_url", None) else [],
                budget_tier=getattr(s_loc, "budget_tier", "Low"),
                permit_status=getattr(s_loc, "permit_status", "Required"),
                vibe_match_score=getattr(s_loc, "vibe_match_score", None),
                vibe_reasoning=getattr(s_loc, "vibe_reasoning", None),
                street_view_url=getattr(s_loc, "street_view_url", None),
                embedding=getattr(s_loc, "embedding", None),
                similar_place_ids=getattr(s_loc, "similar_place_ids", []),
            )
            loc.id = lid
            st.locations[lid] = loc
    if not loc:
        raise HTTPException(404, "no such location")
    loc.shortlisted = body.shortlisted
    if body.notes is not None:
        loc.notes = body.notes
    try:
        get_loc_store(x_session_id).toggle_shortlist(lid, body.shortlisted)
    except Exception:
        pass
    reindex_entity(sid, "location", lid)
    return asdict(loc)


class AttachIn(BaseModel):
    scene: int


@router.post("/locations/{lid}/attach")
def attach(lid: str, body: AttachIn, story_id: str | None = None):
    sid = _sid(story_id)
    st = store.story(sid)
    if lid not in st.locations:
        raise HTTPException(404, "no such location")
    loc = st.locations[lid]
    sc = st.scene_by_number(body.scene)
    if not sc:
        raise HTTPException(404, f"no scene {body.scene}")
    if lid not in sc.location_ids:
        sc.location_ids.append(lid)
    if body.scene not in loc.attached_scenes:
        loc.attached_scenes.append(body.scene)
    return {"scene": sc.number, "location_ids": sc.location_ids, "attached_scenes": loc.attached_scenes}


class ToggleSceneIn(BaseModel):
    scene: int
    attached: bool | None = None


@router.post("/locations/{lid}/toggle-scene")
@router.post("/scout/locations/{lid}/toggle-scene")
def toggle_location_scene(
    lid: str,
    body: ToggleSceneIn,
    story_id: str | None = None,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
):
    sid = _sid(story_id)
    st = store.story(sid)
    loc = st.locations.get(lid)
    if not loc:
        s_loc = get_loc_store(x_session_id).get_location(lid)
        if s_loc:
            loc = store.add_location(
                sid,
                name=s_loc.name,
                address=s_loc.address,
                lat=s_loc.lat,
                lng=s_loc.lng,
                maps_url=s_loc.maps_url,
                notes=s_loc.notes or "",
                photos=[{"url": s_loc.photo_url}] if getattr(s_loc, "photo_url", None) else [],
                budget_tier=getattr(s_loc, "budget_tier", "Low"),
                permit_status=getattr(s_loc, "permit_status", "Required"),
                vibe_match_score=getattr(s_loc, "vibe_match_score", None),
                vibe_reasoning=getattr(s_loc, "vibe_reasoning", None),
                street_view_url=getattr(s_loc, "street_view_url", None),
                embedding=getattr(s_loc, "embedding", None),
                similar_place_ids=getattr(s_loc, "similar_place_ids", []),
            )
            loc.id = lid
            st.locations[lid] = loc
    if not loc:
        raise HTTPException(404, "no such location")
    sc = st.scene_by_number(body.scene)
    if not sc:
        raise HTTPException(404, f"no scene {body.scene}")
    if not hasattr(loc, "attached_scenes") or loc.attached_scenes is None:
        loc.attached_scenes = []

    is_on = (body.scene in loc.attached_scenes) if body.attached is None else not body.attached
    if is_on:
        if body.scene in loc.attached_scenes:
            loc.attached_scenes.remove(body.scene)
        if lid in sc.location_ids:
            sc.location_ids.remove(lid)
    else:
        if body.scene not in loc.attached_scenes:
            loc.attached_scenes.append(body.scene)
        if lid not in sc.location_ids:
            sc.location_ids.append(lid)
    reindex_entity(sid, "location", lid)
    return {"location": asdict(loc), "attached_scenes": loc.attached_scenes, "scene_location_ids": sc.location_ids}


@router.post("/scout/similar")
@router.post("/locations/similar")
def scout_similar(
    req: SimilarLocationsRequest,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
):
    target = req.place_id
    if not target and req.embedding:
        target = req.embedding # type: ignore
    s = get_loc_store(x_session_id)
    if not target:
        return s.get_all()[: req.limit]
    loc = s.get_location(str(target)) if isinstance(target, str) else None
    return s.find_similar(loc or target, limit=req.limit)


@router.get("/scout/canvas")
@router.get("/locations/canvas")
def get_scout_canvas(x_session_id: str | None = Header(None, alias="X-Session-ID")):
    return get_loc_store(x_session_id).get_canvas_board()


@router.post("/scout/canvas")
@router.post("/locations/canvas")
def save_scout_canvas(board: CanvasBoard, x_session_id: str | None = Header(None, alias="X-Session-ID")):
    return get_loc_store(x_session_id).update_canvas_board(board)
