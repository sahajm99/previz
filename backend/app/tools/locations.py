from app.agents.location_scout_agent import scout_agent
from app.models import LocationSuggestion, VibeSearchRequest

def find_locations(scene_description: str, region: str | None = None) -> list[LocationSuggestion]:
    try:
        req = VibeSearchRequest(query=scene_description, region=region, limit=3)
        return scout_agent.scout_locations(req, fallback_to_seed=False)
    except Exception:
        return []
