"""Unit tests for Location Scout Agent and vibe matching scoring."""
import httpx
import pytest
import respx
from app.agents.location_scout_agent import _generate_synthetic_embedding, scout_agent
from app.models import VibeSearchRequest


def test_synthetic_embedding_deterministic():
    text = "Retro Noir Diner 1980s"
    vec1 = _generate_synthetic_embedding(text, dims=768)
    vec2 = _generate_synthetic_embedding(text, dims=768)
    assert len(vec1) == 768
    assert vec1 == vec2
    # Ensure normalized vector
    norm = sum(x * x for x in vec1) ** 0.5
    assert abs(norm - 1.0) < 0.01


@respx.mock
def test_scout_locations_with_places_api():
    respx.post("https://places.googleapis.com/v1/places:searchText").mock(
        return_value=httpx.Response(200, json={"places": [{
            "displayName": {"text": "Neon Diner 84"},
            "formattedAddress": "500 8th Ave, New York, NY",
            "location": {"latitude": 40.75, "longitude": -73.99},
            "id": "diner_84",
            "rating": 4.7,
            "types": ["restaurant", "food", "establishment"],
            "priceLevel": "PRICE_LEVEL_INEXPENSIVE"
        }]})
    )
    req = VibeSearchRequest(query="neon diner in NYC", limit=3)
    locs = scout_agent.scout_locations(req, fallback_to_seed=False)
    assert len(locs) == 1
    assert locs[0].name == "Neon Diner 84"
    assert locs[0].budget_tier == "Free"
    assert locs[0].vibe_match_score is not None
    assert locs[0].vibe_match_score >= 0.7
    assert len(locs[0].embedding) == 768 # type: ignore


def test_scout_locations_fallback_to_seed_when_offline():
    # Calling with a query when API is unconfigured/offline and fallback_to_seed=True should return rich seed locations
    req = VibeSearchRequest(query="retro diner red booth noir", limit=3)
    locs = scout_agent.scout_locations(req, fallback_to_seed=True)
    assert len(locs) >= 1
    assert any("diner" in (l.name + " " + " ".join(l.place_types)).lower() for l in locs)
