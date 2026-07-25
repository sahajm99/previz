"""In-memory and JSON disk-backed location store with numpy cosine similarity search.

Adheres to docs/NOW.md hackathon architecture: instant retrieval without database
provisioning overhead.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from app.config import settings
from app.models import CanvasBoard, CanvasConnection, CanvasNode, LocationSuggestion
from app.services.google_maps import calculate_travel_times

# Default seed locations for out-of-the-box demo functionality
_SEED_LOCATIONS = [
    LocationSuggestion(
        id="seed_noir_diner",
        name="Empire Diner 1980s Retro",
        address="210 10th Ave, New York, NY 10011",
        lat=40.7466,
        lng=-74.0042,
        maps_url="https://www.google.com/maps/place/?q=place_id:seed_noir_diner",
        photo_url="https://images.unsplash.com/photo-1555396273-367ea4eb4db5?auto=format&fit=crop&w=800&q=80",
        place_types=["restaurant", "diner", "food"],
        rating=4.5,
        budget_tier="Low",
        permit_status="Available under $3k/day",
        tech_reqs=["Power 100A available", "Street noise moderate"],
        vibe_match_score=0.94,
        vibe_reasoning="Classic red vinyl booths, reflective chrome counters, and neon signage perfect for moody noir or 1980s aesthetic.",
        street_view_url="https://images.unsplash.com/photo-1555396273-367ea4eb4db5?auto=format&fit=crop&w=800&q=80",
        embedding=[0.8, -0.2, 0.5, 0.1, -0.3, 0.7, 0.2, 0.1] * 96,  # 768 dims
        similar_place_ids=["seed_brooklyn_warehouse", "seed_rooftop_lounge"],
    ),
    LocationSuggestion(
        id="seed_brooklyn_warehouse",
        name="Red Hook Abandoned Warehouse & Dock",
        address="175 Van Dyke St, Brooklyn, NY 11231",
        lat=40.6749,
        lng=-74.0175,
        maps_url="https://www.google.com/maps/place/?q=place_id:seed_brooklyn_warehouse",
        photo_url="https://images.unsplash.com/photo-1513694203232-719a280e022f?auto=format&fit=crop&w=800&q=80",
        place_types=["warehouse", "industrial", "point_of_interest"],
        rating=4.2,
        budget_tier="Low",
        permit_status="NYC Mayor's Office of Media Permit Required",
        tech_reqs=["Generator required for heavy lighting", "High sound isolation"],
        vibe_match_score=0.91,
        vibe_reasoning="Raw brick architecture, exposed industrial beams, and sweeping waterfront views. Ideal for interrogation scenes or climactic confrontations.",
        street_view_url="https://images.unsplash.com/photo-1513694203232-719a280e022f?auto=format&fit=crop&w=800&q=80",
        embedding=[0.3, 0.7, -0.4, 0.6, 0.2, -0.1, 0.5, -0.2] * 96,
        similar_place_ids=["seed_noir_diner", "seed_queens_foundry"],
    ),
    LocationSuggestion(
        id="seed_rooftop_lounge",
        name="Skyline Penthouse & Glass Conservatory",
        address="45 E 45th St, New York, NY 10017",
        lat=40.7533,
        lng=-73.9774,
        maps_url="https://www.google.com/maps/place/?q=place_id:seed_rooftop_lounge",
        photo_url="https://images.unsplash.com/photo-1533105079780-92b9be482077?auto=format&fit=crop&w=800&q=80",
        place_types=["bar", "night_club", "establishment"],
        rating=4.7,
        budget_tier="High",
        permit_status="Private location release required ($5k-$10k/day)",
        tech_reqs=["Dedicated elevator access", "Strict 2AM wrap time"],
        vibe_match_score=0.88,
        vibe_reasoning="Panoramic night views of Midtown skyline, sleek glass balconies, and upscale lounge lighting for sophisticated thriller sequences.",
        street_view_url="https://images.unsplash.com/photo-1533105079780-92b9be482077?auto=format&fit=crop&w=800&q=80",
        embedding=[0.7, 0.4, 0.2, -0.5, 0.6, 0.3, -0.2, 0.4] * 96,
        similar_place_ids=["seed_noir_diner"],
    ),
    LocationSuggestion(
        id="seed_queens_foundry",
        name="Long Island City Steel Foundry & Arches",
        address="43-01 22nd St, Long Island City, NY 11101",
        lat=40.7512,
        lng=-73.9456,
        maps_url="https://www.google.com/maps/place/?q=place_id:seed_queens_foundry",
        photo_url="https://images.unsplash.com/photo-1565008447742-97f6f38c985c?auto=format&fit=crop&w=800&q=80",
        place_types=["establishment", "industrial"],
        rating=4.4,
        budget_tier="Free",
        permit_status="Open public industrial zoning / student friendly",
        tech_reqs=["No power on site", "Natural lighting dominant"],
        vibe_match_score=0.85,
        vibe_reasoning="Gritty urban textures, rusted iron architecture, and dramatic shadows under elevated subway tracks.",
        street_view_url="https://images.unsplash.com/photo-1565008447742-97f6f38c985c?auto=format&fit=crop&w=800&q=80",
        embedding=[0.2, 0.8, -0.5, 0.5, 0.1, -0.2, 0.6, -0.1] * 96,
        similar_place_ids=["seed_brooklyn_warehouse"],
    ),
]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class LocationStore:
    def __init__(self) -> None:
        self._locations: dict[str, LocationSuggestion] = {}
        self._shortlist_ids: set[str] = set()
        self._canvas_board: CanvasBoard = CanvasBoard()
        self._load_from_disk()

    def _get_db_path(self) -> Path:
        p = Path(__file__).parent.parent.parent / settings.locations_db_file
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _get_canvas_path(self) -> Path:
        p = Path(__file__).parent.parent.parent / settings.canvas_db_file
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _load_from_disk(self) -> None:
        db_path = self._get_db_path()
        if db_path.exists() and db_path.stat().st_size > 0:
            try:
                data = json.loads(db_path.read_text("utf-8"))
                for loc_data in data.get("locations", []):
                    loc = LocationSuggestion.model_validate(loc_data)
                    loc_id = loc.id or loc.name
                    self._locations[loc_id] = loc
                self._shortlist_ids = set(data.get("shortlist_ids", []))
            except Exception:
                pass

        # Seed defaults if empty
        if not self._locations:
            for seed in _SEED_LOCATIONS:
                seed_id = seed.id or seed.name
                self._locations[seed_id] = seed
            self._shortlist_ids = {"seed_noir_diner", "seed_brooklyn_warehouse"}
            self._save_to_disk()

        canvas_path = self._get_canvas_path()
        if canvas_path.exists() and canvas_path.stat().st_size > 0:
            try:
                data = json.loads(canvas_path.read_text("utf-8"))
                self._canvas_board = CanvasBoard.model_validate(data)
            except Exception:
                pass
        else:
            # Seed default canvas
            self._canvas_board = CanvasBoard(
                nodes=[
                    CanvasNode(
                        node_id="node_1",
                        scene_id="SCENE 1",
                        scene_name="INT. NOIR BAR - NIGHT",
                        location_id="seed_noir_diner",
                        location=self._locations.get("seed_noir_diner"),
                        time_of_day="Night",
                        x=120,
                        y=80,
                        notes="Needs low-key lighting and rain effects outside windows.",
                    ),
                    CanvasNode(
                        node_id="node_2",
                        scene_id="SCENE 2",
                        scene_name="EXT. RED HOOK DOCK - DAWN",
                        location_id="seed_brooklyn_warehouse",
                        location=self._locations.get("seed_brooklyn_warehouse"),
                        time_of_day="Magic Hour",
                        x=520,
                        y=160,
                        notes="Generator truck parking required by gate 4.",
                    ),
                ],
                connections=[
                    CanvasConnection(
                        from_node="node_1",
                        to_node="node_2",
                        travel_time_mins=28,
                        distance_km=9.4,
                    )
                ],
                logistics_summary={
                    "total_scenes": 2,
                    "est_travel_time_mins": 28,
                    "notes": "Move production units during lunch break.",
                },
            )
            self._save_canvas_to_disk()

    def _save_to_disk(self) -> None:
        db_path = self._get_db_path()
        data = {
            "locations": [loc.model_dump() for loc in self._locations.values()],
            "shortlist_ids": list(self._shortlist_ids),
        }
        try:
            db_path.write_text(json.dumps(data, indent=2), "utf-8")
        except Exception:
            pass

    def _save_canvas_to_disk(self) -> None:
        canvas_path = self._get_canvas_path()
        try:
            canvas_path.write_text(json.dumps(self._canvas_board.model_dump(), indent=2), "utf-8")
        except Exception:
            pass

    def get_all(self) -> list[LocationSuggestion]:
        return list(self._locations.values())

    def get_shortlist(self) -> list[LocationSuggestion]:
        return [self._locations[lid] for lid in self._shortlist_ids if lid in self._locations]

    def get_location(self, loc_id: str) -> LocationSuggestion | None:
        return self._locations.get(loc_id)

    def save_location(self, loc: LocationSuggestion, shortlist: bool = False) -> LocationSuggestion:
        loc_id = loc.id or loc.name
        loc.id = loc_id
        self._locations[loc_id] = loc
        if shortlist:
            self._shortlist_ids.add(loc_id)
        self._save_to_disk()
        return loc

    def toggle_shortlist(self, loc: LocationSuggestion, shortlisted: bool) -> LocationSuggestion:
        loc_id = loc.id or loc.name
        loc.id = loc_id
        self._locations[loc_id] = loc
        if shortlisted:
            self._shortlist_ids.add(loc_id)
        elif loc_id in self._shortlist_ids:
            self._shortlist_ids.remove(loc_id)
        self._save_to_disk()
        return loc

    def find_similar(
        self, target: LocationSuggestion | list[float], limit: int = 3
    ) -> list[LocationSuggestion]:
        target_vec: list[float] | None = None
        exclude_id: str | None = None

        if isinstance(target, LocationSuggestion):
            target_vec = target.embedding
            exclude_id = target.id or target.name
        else:
            target_vec = target

        if not target_vec:
            # Fallback: return other seed locations
            res = [loc for loc in self._locations.values() if (loc.id or loc.name) != exclude_id]
            return res[:limit]

        scored: list[tuple[float, LocationSuggestion]] = []
        for loc in self._locations.values():
            loc_id = loc.id or loc.name
            if loc_id == exclude_id:
                continue
            if loc.embedding:
                score = _cosine_similarity(target_vec, loc.embedding)
            else:
                score = 0.5  # default baseline if no embedding
            scored.append((score, loc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [loc for _, loc in scored[:limit]]

    def get_canvas_board(self) -> CanvasBoard:
        return self._canvas_board

    def update_canvas_board(self, board: CanvasBoard) -> CanvasBoard:
        # Populate location details on nodes if missing
        origins = []
        destinations = []
        for node in board.nodes:
            if node.location_id and node.location_id in self._locations:
                node.location = self._locations[node.location_id]

        # Calculate travel times via Distance Matrix if API key exists and connections need routing
        if settings.google_maps_api_key and len(board.nodes) >= 2 and board.connections:
            node_map = {n.node_id: n for n in board.nodes}
            for conn in board.connections:
                fn = node_map.get(conn.from_node)
                tn = node_map.get(conn.to_node)
                if fn and tn and fn.location and tn.location:
                    res = calculate_travel_times(
                        [(fn.location.lat, fn.location.lng)],
                        [(tn.location.lat, tn.location.lng)],
                    )
                    try:
                        elem = res["rows"][0]["elements"][0]
                        if elem["status"] == "OK":
                            conn.travel_time_mins = max(1, int(elem["duration"]["value"] / 60))
                            conn.distance_km = round(elem["distance"]["value"] / 1000.0, 1)
                    except Exception:
                        pass

        # Update logistics summary
        total_time = sum(c.travel_time_mins or 15 for c in board.connections)
        board.logistics_summary = {
            "total_scenes": len(board.nodes),
            "est_travel_time_mins": total_time,
            "notes": f"Logistics calculated for {len(board.nodes)} scene locations.",
        }

        self._canvas_board = board
        self._save_canvas_to_disk()
        return board

    def query_context(self, scene_description: str, limit: int = 3) -> list[LocationSuggestion]:
        """Knowledge base endpoint for Script Assistant and Storyboarding agents."""
        query_lower = scene_description.lower()
        scored: list[tuple[float, LocationSuggestion]] = []
        for loc in self._locations.values():
            score = 0.0
            if any(w in (loc.name + " " + loc.address).lower() for w in query_lower.split()):
                score += 0.4
            if any(t.lower() in query_lower for t in loc.place_types):
                score += 0.3
            if loc.vibe_reasoning and any(w in loc.vibe_reasoning.lower() for w in query_lower.split()):
                score += 0.3
            if (loc.id or loc.name) in self._shortlist_ids:
                score += 0.2
            scored.append((score, loc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [loc for _, loc in scored[:limit]]


# Singleton store instance
store = LocationStore()
