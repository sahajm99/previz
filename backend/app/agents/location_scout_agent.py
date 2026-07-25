"""Vertex AI Location Scout Agent for multi-modal vibe matching and natural language scouting.

Implements an autonomous reasoning loop using Gemini 2.5 and Google Maps tools.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import random
import re
from typing import Any

from google.genai import types

from app.config import settings
from app.gemini_client import TEXT_MODEL, get_client
from app.models import LocationSuggestion, VibeSearchRequest
from app.services.google_maps import (
    fetch_and_cache_photo,
    get_place_details,
    get_static_map_url,
    get_street_view_url,
    search_text,
)
from app.services.location_store import get_store, store


def _generate_synthetic_embedding(text: str, dims: int = 768) -> list[float]:
    """Generate a deterministic synthetic embedding vector from text for local similarity demos."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    vec = []
    for i in range(dims):
        byte_val = h[i % len(h)]
        val = (byte_val / 128.0) - 1.0 + (math.sin(i) * 0.1)
        vec.append(round(val, 4))
    norm = math.sqrt(sum(x * x for x in vec))
    return [round(x / norm, 4) for x in vec] if norm else [0.0] * dims


class LocationScoutAgent:
    def __init__(self) -> None:
        self.client = None
        try:
            self.client = get_client()
        except Exception:
            pass

    def _parse_query_with_gemini(self, req: VibeSearchRequest) -> dict[str, Any]:
        """Use Gemini 2.5 Flash to parse natural language cinematic prompts into search parameters."""
        default_res = {
            "search_query": req.query,
            "region": req.region or "New York, NY",
            "included_type": "restaurant" if "diner" in req.query.lower() else "establishment",
            "aesthetic_vibe": req.query,
            "budget_tier": req.budget_tier or "Low",
            "time_of_day": req.time_of_day or "Night",
        }
        if not self.client:
            return default_res

        prompt = (
            "You are an expert location scout for feature films. Parse this director's natural language request "
            "into concrete search parameters for Google Maps Places API.\n\n"
            f"Director Prompt: \"{req.query}\"\n"
            f"Region Override: {req.region}\n"
            f"Budget Constraint: {req.budget_tier}\n"
            f"Time of Day: {req.time_of_day}\n\n"
            "Return JSON with exactly these keys:\n"
            '  "search_query": concise text query for Places API (e.g., "retro diner neon", "abandoned warehouse").\n'
            '  "region": target geographic area (city/state).\n'
            '  "included_type": Google Places type (e.g. restaurant, bar, point_of_interest, establishment, warehouse).\n'
            '  "aesthetic_vibe": description of visual textures, lighting, and mood needed.\n'
            '  "budget_tier": "Free", "Low", or "High".\n'
            '  "time_of_day": "Day", "Night", or "Magic Hour".\n'
        )

        try:
            resp = self.client.models.generate_content(
                model=TEXT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            data = json.loads(resp.text)
            if isinstance(data, dict) and "search_query" in data:
                return data
        except Exception:
            pass
        return default_res

    def _score_vibe_match(self, place: dict[str, Any], vibe_desc: str) -> tuple[float, str]:
        """Score how well a place matches the cinematic vibe (0.0 to 1.0) with heuristic reasoning."""
        name = (place.get("displayName") or {}).get("text", "Unknown Place")
        address = place.get("formattedAddress", "")
        types_list = place.get("types", [])
        rating = place.get("rating", 4.0)

        score = 0.75
        reasons = []

        vibe_words = set(re.findall(r"\w+", vibe_desc.lower()))
        place_words = set(re.findall(r"\w+", (name + " " + address + " " + " ".join(types_list)).lower()))
        overlap = len(vibe_words.intersection(place_words))

        if overlap > 0:
            score = min(0.98, 0.82 + (overlap * 0.05))
            reasons.append(f"Strong semantic alignment on terms: {', '.join(vibe_words.intersection(place_words))}.")
        else:
            reasons.append(f"Matches general category ({types_list[0] if types_list else 'location'}) in target region.")

        if rating >= 4.5:
            score = min(0.99, score + 0.03)
            reasons.append("Highly rated location with proven production reliability.")

        return round(score, 2), " ".join(reasons)

    def scout_locations(self, req: VibeSearchRequest, fallback_to_seed: bool = True, session_id: str | None = None) -> list[LocationSuggestion]:
        """Execute autonomous scouting loop: parse -> search -> concurrent enrich & batch score -> store."""
        parsed = self._parse_query_with_gemini(req)
        query_str = parsed.get("search_query", req.query)
        region_str = parsed.get("region", req.region or "")
        vibe_str = parsed.get("aesthetic_vibe", req.query)
        inc_type = parsed.get("included_type", None)

        places = search_text(query_str, max_results=req.limit, region=region_str, included_type=inc_type)

        if not places:
            if not fallback_to_seed:
                return []
            all_locs = get_store(session_id).get_all()
            q_terms = set(re.findall(r"\w+", req.query.lower()))
            matched = []
            for loc in all_locs:
                l_terms = set(re.findall(r"\w+", (loc.name + " " + loc.address + " " + (loc.vibe_reasoning or "")).lower()))
                ov = len(q_terms.intersection(l_terms))
                if ov > 0 or not q_terms:
                    matched.append((ov, loc))
            matched.sort(key=lambda x: x[0], reverse=True)
            res = [m[1] for m in matched[: req.limit]]
            return res if res else all_locs[: req.limit]

        target_places = places[: req.limit]
        batch_evals = {}
        if self.client and target_places:
            try:
                place_items = []
                for idx, p in enumerate(target_places):
                    p_name = (p.get("displayName") or {}).get("text", "Unknown")
                    p_addr = p.get("formattedAddress", "")
                    p_types = p.get("types", ["location"])
                    place_items.append(f"[{idx}] Name: {p_name} | Address: {p_addr} | Types: {', '.join(p_types[:3])}")
                prompt = (
                    f"Evaluate how well each location fits a film director's vibe requirement.\n"
                    f"Director Vibe Requirement: \"{vibe_str}\"\n\n"
                    f"Locations:\n" + "\n".join(place_items) + "\n\n"
                    "Return a JSON object where each key is the location index (e.g. \"0\", \"1\") and value is an object with \"score\" (float between 0.70 and 0.99) and \"reasoning\" (1 concise sentence explaining why this location is visually compelling for this scene)."
                )
                resp = self.client.models.generate_content(
                    model=TEXT_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                data = json.loads(resp.text)
                if isinstance(data, dict):
                    batch_evals = data
            except Exception:
                pass

        def _process_place(args: tuple[int, dict[str, Any]]) -> LocationSuggestion:
            idx, p = args
            pid = p.get("id", "")
            name = (p.get("displayName") or {}).get("text", "Unknown")
            addr = p.get("formattedAddress", "")
            loc_data = p.get("location") or {}
            lat = loc_data.get("latitude", 40.7128)
            lng = loc_data.get("longitude", -74.0060)
            rating = p.get("rating", 4.2)
            types_list = p.get("types", ["establishment"])
            price_level = p.get("priceLevel", "PRICE_LEVEL_MODERATE")

            b_tier = "Low"
            if price_level in ("PRICE_LEVEL_EXPENSIVE", "PRICE_LEVEL_VERY_EXPENSIVE"):
                b_tier = "High"
            elif price_level in ("PRICE_LEVEL_FREE", "PRICE_LEVEL_INEXPENSIVE"):
                b_tier = "Free"

            photos = p.get("photos", [])
            photo_url = None
            photo_refs = []
            if photos:
                for ph in photos[:3]:
                    pref = ph.get("name") or ph.get("photo_reference")
                    if pref:
                        photo_refs.append(pref)
                if photo_refs:
                    photo_url = fetch_and_cache_photo(photo_refs[0])

            sv_url = get_street_view_url(lat, lng)
            if not photo_url:
                photo_url = sv_url or get_static_map_url(lat, lng)

            eval_data = batch_evals.get(str(idx)) or batch_evals.get(idx)
            if isinstance(eval_data, dict) and "score" in eval_data and "reasoning" in eval_data:
                try:
                    score = round(float(eval_data["score"]), 2)
                    reasoning = str(eval_data["reasoning"]).strip()
                except Exception:
                    score, reasoning = self._score_vibe_match(p, vibe_str)
            else:
                score, reasoning = self._score_vibe_match(p, vibe_str)

            emb = _generate_synthetic_embedding(f"{name} {addr} {vibe_str} {' '.join(types_list)}")

            loc = LocationSuggestion(
                id=pid or f"loc_{abs(hash(name))}",
                name=name,
                address=addr,
                lat=lat,
                lng=lng,
                maps_url=f"https://www.google.com/maps/place/?q=place_id:{pid}" if pid else f"https://www.google.com/maps/search/?api=1&query={lat},{lng}",
                photo_url=photo_url,
                place_types=types_list[:4],
                rating=rating,
                budget_tier=b_tier,  # type: ignore
                permit_status="Standard municipal permit required" if b_tier != "Free" else "No permit needed for small crews",
                tech_reqs=["Power access via nearby street drop", "Low RF interference"],
                vibe_match_score=score,
                vibe_reasoning=reasoning,
                photo_references=photo_refs,
                street_view_url=sv_url,
                embedding=emb,
            )
            get_store(session_id).save_location(loc)
            return loc

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(_process_place, enumerate(target_places)))

        return results


scout_agent = LocationScoutAgent()
