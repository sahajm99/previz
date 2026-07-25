"""Integration tests for FastAPI locations router endpoints."""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models import LocationSuggestion

client = TestClient(app)


def test_get_all_locations():
    res = client.get("/api/v1/locations/all")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 2
    assert any("Empire Diner" in loc["name"] for loc in data)


def test_get_and_toggle_shortlist():
    res = client.get("/api/v1/locations/shortlist")
    assert res.status_code == 200
    initial = res.json()

    # Test toggling a seed location out and back in
    target = initial[0] if initial else {
        "id": "test_loc_id", "name": "Test Loc", "address": "123 St",
        "lat": 40.0, "lng": -74.0, "maps_url": "https://maps", "budget_tier": "Low",
        "permit_status": "Required", "place_types": []
    }
    
    toggle_res = client.post("/api/v1/locations/shortlist", json={
        "location": target,
        "shortlisted": False
    })
    assert toggle_res.status_code == 200


def test_similarity_search_endpoint():
    res = client.post("/api/v1/locations/similar", json={
        "place_id": "seed_noir_diner",
        "limit": 2
    })
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) <= 2


def test_canvas_board_endpoints():
    res = client.get("/api/v1/locations/canvas")
    assert res.status_code == 200
    board = res.json()
    assert "nodes" in board
    assert "connections" in board
    assert "logistics_summary" in board
    
    # Test updating board
    if board["nodes"]:
        board["nodes"][0]["notes"] = "Updated test note"
    update_res = client.post("/api/v1/locations/canvas", json=board)
    assert update_res.status_code == 200
    assert update_res.json()["nodes"][0]["notes"] == "Updated test note"


def test_query_context_endpoint():
    res = client.get("/api/v1/locations/context?scene_description=diner noir night&limit=2")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
