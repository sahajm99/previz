"""Unit tests for Google Maps API service wrapper."""
from pathlib import Path
import httpx
import pytest
import respx
from app.services.google_maps import (
    _safe_filename,
    calculate_travel_times,
    fetch_and_cache_photo,
    get_place_details,
    get_static_map_url,
    get_street_view_url,
    search_nearby,
    search_text,
)


def test_safe_filename():
    assert _safe_filename("simple_name") == "simple_name"
    long_ref = "places/ChIJ-test-12345678901234567890123456789012345678901234567890/photos/abc"
    clean = _safe_filename(long_ref)
    assert len(clean) <= 52
    assert "/" not in clean


@respx.mock
def test_search_text_returns_places():
    respx.post("https://places.googleapis.com/v1/places:searchText").mock(
        return_value=httpx.Response(200, json={"places": [{
            "displayName": {"text": "Retro Diner"},
            "formattedAddress": "100 Broadway, NY",
            "location": {"latitude": 40.71, "longitude": -74.00},
            "id": "place_diner_1",
            "rating": 4.6,
            "types": ["restaurant", "diner"]
        }]})
    )
    res = search_text("retro diner in New York")
    assert len(res) == 1
    assert res[0]["displayName"]["text"] == "Retro Diner"
    assert res[0]["id"] == "place_diner_1"


@respx.mock
def test_search_nearby_returns_places():
    respx.post("https://places.googleapis.com/v1/places:searchNearby").mock(
        return_value=httpx.Response(200, json={"places": [{
            "displayName": {"text": "Dock Warehouse"},
            "id": "place_dock_1"
        }]})
    )
    res = search_nearby(40.67, -74.01, radius_meters=1500)
    assert len(res) == 1
    assert res[0]["id"] == "place_dock_1"


@respx.mock
def test_get_place_details():
    respx.get("https://places.googleapis.com/v1/places/test_id").mock(
        return_value=httpx.Response(200, json={"id": "test_id", "displayName": {"text": "Test Place"}})
    )
    res = get_place_details("test_id")
    assert res.get("id") == "test_id"
    assert res.get("displayName", {}).get("text") == "Test Place"


@respx.mock
def test_calculate_travel_times():
    respx.get(url__regex=r"^https://maps\.googleapis\.com/maps/api/distancematrix/json.*").mock(
        return_value=httpx.Response(200, json={
            "rows": [{
                "elements": [{
                    "status": "OK",
                    "duration": {"value": 1680, "text": "28 mins"},
                    "distance": {"value": 9400, "text": "9.4 km"}
                }]
            }]
        })
    )
    res = calculate_travel_times([(40.74, -74.00)], [(40.67, -74.01)])
    assert "rows" in res
    elem = res["rows"][0]["elements"][0]
    assert elem["duration"]["value"] == 1680
    assert elem["distance"]["value"] == 9400
