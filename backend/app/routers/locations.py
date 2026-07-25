"""FastAPI endpoints for Location Scouting, Similarity Search, and Interactive Scene Canvas.

Provides clean endpoints for UI interaction and Knowledge Base queries by other agents.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from app.agents.location_scout_agent import scout_agent
from app.models import (
    CanvasBoard,
    ContextQueryRequest,
    LocationSuggestion,
    ShortlistToggleRequest,
    SimilarLocationsRequest,
    VibeSearchRequest,
)
from app.services.location_store import store

router = APIRouter(prefix="/api/v1/locations", tags=["Location Scouting & Scene Canvas"])


@router.post("/search", response_model=list[LocationSuggestion])
def search_locations(req: VibeSearchRequest) -> list[LocationSuggestion]:
    """Execute natural language vibe search and return scored location suggestions."""
    return scout_agent.scout_locations(req)


@router.post("/similar", response_model=list[LocationSuggestion])
def find_similar_locations(req: SimilarLocationsRequest) -> list[LocationSuggestion]:
    """Vector similarity search: return locations visually and semantically similar to target."""
    target = req.place_id
    if not target and req.embedding:
        target = req.embedding # type: ignore
    if not target:
        return store.get_all()[: req.limit]
    loc = store.get_location(str(target)) if isinstance(target, str) else None
    return store.find_similar(loc or target, limit=req.limit)


@router.get("/all", response_model=list[LocationSuggestion])
def get_all_locations() -> list[LocationSuggestion]:
    """Return all saved and cached locations in the database."""
    return store.get_all()


@router.get("/shortlist", response_model=list[LocationSuggestion])
def get_shortlisted_locations() -> list[LocationSuggestion]:
    """Return director's shortlisted locations."""
    return store.get_shortlist()


@router.post("/shortlist", response_model=LocationSuggestion)
def toggle_shortlist(req: ShortlistToggleRequest) -> LocationSuggestion:
    """Add or remove a location from the saved shortlist."""
    return store.toggle_shortlist(req.location, req.shortlisted)


@router.get("/canvas", response_model=CanvasBoard)
def get_scene_canvas() -> CanvasBoard:
    """Fetch the current interactive spatial scene canvas board layout."""
    return store.get_canvas_board()


@router.post("/canvas", response_model=CanvasBoard)
def update_scene_canvas(board: CanvasBoard) -> CanvasBoard:
    """Update canvas node coordinates, scene assignments, and travel logistics."""
    return store.update_canvas_board(board)


@router.post("/context", response_model=list[LocationSuggestion])
@router.get("/context", response_model=list[LocationSuggestion])
def query_knowledge_base_context(
    scene_description: str = Query(..., description="Screenplay scene description or query text"),
    limit: int = Query(3, description="Max number of locations to return"),
) -> list[LocationSuggestion]:
    """Knowledge Base Integration endpoint.
    
    Allows Script Assistant and Storyboarding agents to query saved locations by scene text or vibe
    so they can suggest real saved places while writing screenplays.
    """
    return store.query_context(scene_description, limit=limit)
