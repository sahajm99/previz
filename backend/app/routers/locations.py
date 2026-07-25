"""FastAPI endpoints for Location Scouting, Similarity Search, and Interactive Scene Canvas.

Provides clean endpoints for UI interaction and Knowledge Base queries by other agents,
with multi-user session isolation via X-Session-ID headers for hackathon demos.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query
from app.agents.location_scout_agent import scout_agent
from app.models import (
    CanvasBoard,
    ContextQueryRequest,
    LocationSuggestion,
    ShortlistToggleRequest,
    SimilarLocationsRequest,
    VibeSearchRequest,
)
from app.services.location_store import get_store, store

router = APIRouter(prefix="/api/v1/locations", tags=["Location Scouting & Scene Canvas"])


@router.post("/search", response_model=list[LocationSuggestion])
def search_locations(
    req: VibeSearchRequest,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
) -> list[LocationSuggestion]:
    """Execute natural language vibe search and return scored location suggestions."""
    return scout_agent.scout_locations(req, session_id=x_session_id)


@router.post("/similar", response_model=list[LocationSuggestion])
def find_similar_locations(
    req: SimilarLocationsRequest,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
) -> list[LocationSuggestion]:
    """Vector similarity search: return locations visually and semantically similar to target."""
    target = req.place_id
    if not target and req.embedding:
        target = req.embedding # type: ignore
    s = get_store(x_session_id)
    if not target:
        return s.get_all()[: req.limit]
    loc = s.get_location(str(target)) if isinstance(target, str) else None
    return s.find_similar(loc or target, limit=req.limit)


@router.get("/all", response_model=list[LocationSuggestion])
def get_all_locations(
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
) -> list[LocationSuggestion]:
    """Return all saved and cached locations in the database."""
    return get_store(x_session_id).get_all()


@router.get("/shortlist", response_model=list[LocationSuggestion])
def get_shortlisted_locations(
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
) -> list[LocationSuggestion]:
    """Return director's shortlisted locations."""
    return get_store(x_session_id).get_shortlist()


@router.post("/shortlist", response_model=LocationSuggestion)
def toggle_shortlist(
    req: ShortlistToggleRequest,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
) -> LocationSuggestion:
    """Add or remove a location from the saved shortlist."""
    res = get_store(x_session_id).toggle_shortlist(req.location, req.shortlisted)
    try:
        from app.store import store
        st = store.story(None)
        if res.id in st.locations:
            st.locations[res.id].shortlisted = res.shortlisted
        else:
            loc = store.add_location(
                None,
                name=res.name,
                address=res.address,
                lat=res.lat,
                lng=res.lng,
                maps_url=res.maps_url,
                notes=res.notes or "",
                photos=[{"url": res.photo_url}] if getattr(res, "photo_url", None) else [],
                budget_tier=getattr(res, "budget_tier", "Low"),
                permit_status=getattr(res, "permit_status", "Required"),
                vibe_match_score=getattr(res, "vibe_match_score", None),
                vibe_reasoning=getattr(res, "vibe_reasoning", None),
                street_view_url=getattr(res, "street_view_url", None),
                embedding=getattr(res, "embedding", None),
                similar_place_ids=getattr(res, "similar_place_ids", []),
            )
            loc.id = res.id
            loc.shortlisted = res.shortlisted
            st.locations[res.id] = loc
    except Exception:
        pass
    return res


@router.get("/canvas", response_model=CanvasBoard)
def get_scene_canvas(
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
) -> CanvasBoard:
    """Fetch the current interactive spatial scene canvas board layout."""
    return get_store(x_session_id).get_canvas_board()


@router.post("/canvas", response_model=CanvasBoard)
def update_scene_canvas(
    board: CanvasBoard,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
) -> CanvasBoard:
    """Update canvas node coordinates, scene assignments, and travel logistics."""
    return get_store(x_session_id).update_canvas_board(board)


@router.post("/context", response_model=list[LocationSuggestion])
@router.get("/context", response_model=list[LocationSuggestion])
def query_knowledge_base_context(
    scene_description: str = Query(..., description="Screenplay scene description or query text"),
    limit: int = Query(3, description="Max number of locations to return"),
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
) -> list[LocationSuggestion]:
    """Knowledge Base Integration endpoint.
    
    Allows Script Assistant and Storyboarding agents to query saved locations by scene text or vibe
    so they can suggest real saved places while writing screenplays.
    """
    return get_store(x_session_id).query_context(scene_description, limit=limit)
