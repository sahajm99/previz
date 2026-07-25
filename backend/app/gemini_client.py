"""Vertex AI client. Shared by every tab, so fix it here and nowhere else.

Previously this used genai.Client(api_key=...), which targets AI Studio, with
TEXT_MODEL = "gemini-flash-latest". That model id returns 404 on Vertex AI: it is
an AI Studio only alias. So the old configuration could not work once we moved to
Vertex, and every tab would have hit it independently.

Model ids live here and in consistency.py / voice.py only. All verified by real
calls against nyu-ai-builder26nyc-9338 on 2026-07-25 via scripts/probe-models.sh.
The Gemini 3 family returns 404 on this project.
"""
from functools import lru_cache

from google import genai

from app.config import settings

# Verified working. Do not substitute a "-latest" alias, those are AI Studio only.
TEXT_MODEL = "gemini-2.5-flash"          # fast: parsers, scouting, character voices
REASONING_MODEL = "gemini-2.5-pro"       # slow: shot lists, card compilation
CHEAP_MODEL = "gemini-2.5-flash-lite"    # summaries, classification
IMAGE_MODEL = "gemini-2.5-flash-image"   # Nano Banana


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    """Cached, and Vertex mode.

    Cached because constructing a client per call gets it garbage collected mid
    flight, and the next request dies with "Cannot send a request, as the client
    has been closed."

    Vertex mode with no api key: ADC locally, the attached service account on
    Cloud Run. If every call returns 429 RESOURCE_EXHAUSTED, your ADC has no
    quota project:

        gcloud auth application-default set-quota-project nyu-ai-builder26nyc-9338
    """
    return genai.Client(vertexai=True, project=settings.gcp_project,
                        location=settings.gcp_location)


def generate_image(prompt: str, aspect_ratio: str = "16:9") -> bytes:
    """One image from a text prompt, no reference conditioning.

    For anything with a character in it use
    consistency.generate_shot_with_referee() instead: this function has no
    identity conditioning, so faces drift between calls.
    """
    resp = get_client().models.generate_content(
        model=IMAGE_MODEL,
        contents=f"{prompt}. Cinematic {aspect_ratio} widescreen framing.",
    )
    for part in resp.candidates[0].content.parts:
        inline = getattr(part, "inline_data", None)
        if inline and inline.data:
            return inline.data
    raise RuntimeError("model returned no image")
