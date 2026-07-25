"""Previs web app — serves the UI and runs the storyboard pipeline."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.models import StylePreset
from app.pipeline import generate_storyboard
from app.routers.locations import router as locations_router

app = FastAPI(title="Previs")
STATIC = Path(__file__).parent / "static"
CACHE_DIR = Path(__file__).parent.parent / "demo_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

app.include_router(locations_router)
app.mount("/cache", StaticFiles(directory=str(CACHE_DIR)), name="cache")


class GenerateRequest(BaseModel):
    scene: str
    style: StylePreset = StylePreset()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/generate")
def generate(req: GenerateRequest):
    """Scene text + style -> storyboard frames + real filming locations."""
    return generate_storyboard(req.scene, req.style)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
