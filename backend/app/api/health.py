"""Health, deliberately unauthenticated.

A health endpoint behind a login is not a health endpoint. Cloud Run probes, the
deploy script's smoke test and any uptime check all call this without a token, and
if it 401s they all report the service as broken while it is perfectly fine. That
exact bug shipped once here: turning Google Sign-In on made /api/health return 401,
the deploy script called it a failed deploy, and the watcher would have redeployed
in a loop forever.

It reports only counts and configuration flags. No story content, no user data, no
secrets. `client_id` is not here even though it is a public identifier, because a
health probe has no reason to carry it and /api/auth/config already does.

It also lives under /api rather than at /healthz, because Cloud Run's frontend
intercepts /healthz and returns its own 404 without the request ever reaching the
container.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.bible import index
from app.config import settings
from app.store import store

router = APIRouter()


@router.get("/health")
def health():
    sid = store.default_story_id
    chunks = index.for_story(sid) if sid else []
    return {"ok": True,
            "project": settings.gcp_project,
            "location": settings.gcp_location,
            "story_id": sid,
            "stories": len(store.stories),
            "chunks": len(chunks),
            # Retrieval silently degrading to lexical only is worth seeing here
            # rather than inferring from bad search results.
            "chunks_embedded": sum(1 for c in chunks
                                   if c.embedding is not None),
            "maps_key": bool(settings.google_maps_api_key),
            "auth_required": bool(settings.google_oauth_client_id)}
