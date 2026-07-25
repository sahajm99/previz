"""Magic Hour. Serves the UI and mounts the API.

DO NOT ADD ROUTES TO THIS FILE. Add a module under app/api/ and include it in
app/api/__init__.py. That rule exists so several people can build several tabs on
several branches with nothing to conflict on at merge time.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app import images
from app.routers.locations import router as locations_router

STATIC = Path(__file__).parent / "static"
# One directory serves every frame. images.publish_demo_cache() mirrors the
# committed demo_cache into it at startup, so a cached frame and a frame
# generated thirty seconds ago have the same URL shape and the client never has
# to know which is which.
CACHE = images.RUNTIME

SEED_ERROR: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the seeded story from disk. No network call, deliberately.

    The lab project is shared with every other team in the room, so an app whose
    first screen depends on Vertex answering is an app that fails on stage. The
    seed reads the spike's already compiled cards and already generated frames off
    disk, which is why opening the app shows a real story with real cards before
    anyone touches a model.
    """
    global SEED_ERROR
    n = images.publish_demo_cache()
    print(f"  cache: {n} committed frame(s) published to {CACHE.name}/")
    try:
        from app import seed
        seed.build()
    except Exception as exc:                            # noqa: BLE001
        SEED_ERROR = f"{type(exc).__name__}: {exc}"
        print(f"  SEED FAILED: {SEED_ERROR}")
        from app.store import store
        if not store.stories:
            store.create_story("Untitled", "")
    yield


app = FastAPI(title="Magic Hour", lifespan=lifespan)

# One process today, so this is only for a teammate running a separate frontend
# dev server against this API.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

# Mounted defensively on purpose. Several tabs are being written in parallel
# right now, so app/api/ can be momentarily unimportable (a router referenced
# before its module exists). One half-written tab must not stop the app from
# booting, because a demo that will not start is worse than a tab that 404s.
API_ERROR: str | None = None
try:
    from app.api import api

    app.include_router(api)
    print("  api mounted")
except Exception as exc:  # noqa: BLE001
    API_ERROR = f"{type(exc).__name__}: {exc}"
    print(f"  API FAILED TO MOUNT: {API_ERROR}")

app.include_router(locations_router)

# Cached frames, sheets and dialogue, served from disk. The venue wifi dying, the
# lab project expiring and the shared image quota running out are all live risks
# today, and cached assets survive all three.
LOCATIONS_CACHE_DIR = Path(__file__).parent.parent / "demo_cache" / "locations"
LOCATIONS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/cache/locations", StaticFiles(directory=str(LOCATIONS_CACHE_DIR)), name="cache_locations")
if CACHE.is_dir():
    app.mount("/cache", StaticFiles(directory=CACHE), name="cache")

# app.css and app.js. Kept as separate files rather than inlined into index.html
# so each stays under the 300 line rule and so a style change is not a diff
# against the markup.
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/healthz")
def healthz() -> dict:
    """Liveness, plus enough state to tell whether the seed and index came up.

    Reports the chunk count and how many are embedded because retrieval degrades
    to lexical only when embedding fails, and that is a degradation worth seeing
    rather than discovering through bad search results.
    """
    from app.bible import index
    from app.config import settings
    from app.store import store

    sid = store.default_story_id
    chunks = index.for_story(sid) if sid else []
    return {"ok": API_ERROR is None and SEED_ERROR is None,
            "api_error": API_ERROR, "seed_error": SEED_ERROR,
            "project": settings.gcp_project,
            "story_id": sid,
            "chunks": len(chunks),
            "chunks_embedded": sum(1 for c in chunks
                                   if c.embedding is not None),
            "maps_key": bool(settings.google_maps_api_key),
            "routes": sorted({r.path for r in app.routes
                              if getattr(r, "path", "").startswith("/api/")})}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
