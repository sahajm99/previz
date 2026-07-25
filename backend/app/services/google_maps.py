"""Google Maps API wrapper for Places (New), Static Maps, Street View, and Distance Matrix.

Implements disk caching for Places photos and map tiles as directed by docs/NOW.md,
ensuring live demos never fail due to expired URLs or network latency.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

import httpx
from app.config import settings

_SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
_SEARCH_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
_DETAILS_URL = "https://places.googleapis.com/v1/places/"
_DISTANCE_MATRIX_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

_FIELD_MASK = (
    "places.displayName,places.formattedAddress,places.location,places.id,"
    "places.rating,places.types,places.photos,places.priceLevel"
)


def _get_cache_dir() -> Path:
    cache_dir = Path(__file__).parent.parent.parent / settings.locations_cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _safe_filename(ref: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]", "_", ref)
    if len(clean) > 50:
        h = hashlib.md5(ref.encode()).hexdigest()[:10]
        clean = f"{clean[:40]}_{h}"
    return clean


def fetch_and_cache_photo(photo_ref: str, max_width_px: int = 800) -> str | None:
    """Download and cache a photo from Google Places or Maps API to disk.

    Returns relative URL /cache/locations/{filename}.jpg for frontend rendering.
    """
    if not photo_ref:
        return None
    cache_dir = _get_cache_dir()
    filename = f"photo_{_safe_filename(photo_ref)}.jpg"
    filepath = cache_dir / filename
    rel_url = f"/cache/locations/{filename}"

    if filepath.exists() and filepath.stat().st_size > 0:
        return rel_url

    if not settings.google_maps_api_key:
        return None

    try:
        if photo_ref.startswith("places/"):
            url = (
                f"https://places.googleapis.com/v1/{photo_ref}/media"
                f"?maxHeightPx={max_width_px}&maxWidthPx={max_width_px}"
                f"&key={settings.google_maps_api_key}"
            )
        else:
            url = (
                f"https://maps.googleapis.com/maps/api/place/photo"
                f"?maxwidth={max_width_px}&photo_reference={photo_ref}"
                f"&key={settings.google_maps_api_key}"
            )
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            r = client.get(url)
            if r.status_code == 200:
                filepath.write_bytes(r.content)
                return rel_url
    except Exception:
        pass
    return None


def get_static_map_url(lat: float, lng: float, zoom: int = 15, size: str = "600x400") -> str:
    """Generate Static Maps preview URL and optionally cache tile to disk."""
    cache_dir = _get_cache_dir()
    filename = f"map_{lat:.4f}_{lng:.4f}_z{zoom}_{size}.png"
    filepath = cache_dir / filename
    rel_url = f"/cache/locations/{filename}"

    if filepath.exists() and filepath.stat().st_size > 0:
        return rel_url

    url = (
        f"https://maps.googleapis.com/maps/api/staticmap"
        f"?center={lat},{lng}&zoom={zoom}&size={size}"
        f"&markers=color:red%7C{lat},{lng}"
        f"&style=feature:all%7Celement:geometry%7Ccolor:0x242f3e"
        f"&style=feature:all%7Celement:labels.text.stroke%7Ccolor:0x242f3e"
        f"&style=feature:all%7Celement:labels.text.fill%7Ccolor:0x746855"
        f"&key={settings.google_maps_api_key}"
    )

    if settings.google_maps_api_key:
        try:
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                r = client.get(url)
                if r.status_code == 200:
                    filepath.write_bytes(r.content)
                    return rel_url
        except Exception:
            pass
    return url


def get_street_view_url(lat: float, lng: float, size: str = "600x400") -> str:
    """Generate Street View Static API preview URL and cache to disk."""
    cache_dir = _get_cache_dir()
    filename = f"sv_{lat:.4f}_{lng:.4f}_{size}.jpg"
    filepath = cache_dir / filename
    rel_url = f"/cache/locations/{filename}"

    if filepath.exists() and filepath.stat().st_size > 0:
        return rel_url

    url = (
        f"https://maps.googleapis.com/maps/api/streetview"
        f"?size={size}&location={lat},{lng}&fov=90"
        f"&key={settings.google_maps_api_key}"
    )

    if settings.google_maps_api_key:
        try:
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                r = client.get(url)
                if r.status_code == 200:
                    filepath.write_bytes(r.content)
                    return rel_url
        except Exception:
            pass
    return url


def search_text(
    query: str,
    max_results: int = 6,
    region: str | None = None,
    included_type: str | None = None,
) -> list[dict[str, Any]]:
    """Execute Text Search via Google Places API (New)."""
    full_query = query if not region else f"{query} in {region}"
    headers = {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": _FIELD_MASK,
    }
    payload: dict[str, Any] = {"textQuery": full_query, "maxResultCount": max_results}
    if included_type:
        payload["includedType"] = included_type

    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(_SEARCH_TEXT_URL, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                return []
            return data.get("places", [])
    except Exception:
        return []


def search_nearby(
    lat: float,
    lng: float,
    radius_meters: float = 3000.0,
    included_types: list[str] | None = None,
    max_results: int = 6,
) -> list[dict[str, Any]]:
    """Execute Nearby Search via Google Places API (New)."""
    headers = {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": _FIELD_MASK,
    }
    payload: dict[str, Any] = {
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius_meters,
            }
        },
        "maxResultCount": max_results,
    }
    if included_types:
        payload["includedTypes"] = included_types

    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(_SEARCH_NEARBY_URL, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                return []
            return data.get("places", [])
    except Exception:
        return []


def get_place_details(place_id: str) -> dict[str, Any]:
    """Fetch place details by place_id via Google Places API (New)."""
    if not place_id:
        return {}

    url = f"{_DETAILS_URL}{place_id}"
    headers = {
        "X-Goog-Api-Key": settings.google_maps_api_key,
        "X-Goog-FieldMask": _FIELD_MASK,
    }
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def calculate_travel_times(
    origins: list[tuple[float, float]],
    destinations: list[tuple[float, float]],
) -> dict[str, Any]:
    """Calculate travel times and distances between coordinates via Distance Matrix API."""
    if not origins or not destinations:
        return {}

    origins_str = "|".join(f"{lat},{lng}" for lat, lng in origins)
    dest_str = "|".join(f"{lat},{lng}" for lat, lng in destinations)
    params = {
        "origins": origins_str,
        "destinations": dest_str,
        "key": settings.google_maps_api_key,
    }
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(_DISTANCE_MATRIX_URL, params=params)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
