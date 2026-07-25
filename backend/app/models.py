from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

class StylePreset(BaseModel):
    genre: str = "cinematic"
    visual_style: str = "photorealistic"
    mood: str = "neutral"
    aspect_ratio: str = "16:9"
    color_palette: str = "natural"
    era: str = "present day"

class Shot(BaseModel):
    index: int
    description: str
    camera: Optional[str] = None

class LocationSuggestion(BaseModel):
    name: str
    address: str
    lat: float
    lng: float
    maps_url: str
    photo_url: Optional[str] = None
    id: Optional[str] = ""
    place_types: list[str] = Field(default_factory=list)
    rating: Optional[float] = None
    budget_tier: Literal["Free", "Low", "High"] = "Low"
    permit_status: str = "Required"
    tech_reqs: list[str] = Field(default_factory=list)
    vibe_match_score: Optional[float] = None
    vibe_reasoning: Optional[str] = None
    photo_references: list[str] = Field(default_factory=list)
    street_view_url: Optional[str] = None
    embedding: Optional[list[float]] = None
    similar_place_ids: list[str] = Field(default_factory=list)

class VibeSearchRequest(BaseModel):
    query: str
    region: Optional[str] = None
    budget_tier: Optional[str] = None
    time_of_day: Optional[str] = None
    indoor_outdoor: Optional[str] = None
    limit: int = 6

class SimilarLocationsRequest(BaseModel):
    place_id: Optional[str] = None
    embedding: Optional[list[float]] = None
    limit: int = 3

class CanvasNode(BaseModel):
    node_id: str
    scene_id: str
    scene_name: str
    location_id: Optional[str] = None
    location: Optional[LocationSuggestion] = None
    time_of_day: Literal["Day", "Night", "Magic Hour"] = "Day"
    x: float = 0.0
    y: float = 0.0
    notes: Optional[str] = None

class CanvasConnection(BaseModel):
    from_node: str
    to_node: str
    travel_time_mins: Optional[int] = None
    distance_km: Optional[float] = None

class CanvasBoard(BaseModel):
    nodes: list[CanvasNode] = Field(default_factory=list)
    connections: list[CanvasConnection] = Field(default_factory=list)
    logistics_summary: dict[str, Any] = Field(default_factory=dict)

class ShortlistToggleRequest(BaseModel):
    location: LocationSuggestion
    shortlisted: bool = True

class ContextQueryRequest(BaseModel):
    scene_description: str
    limit: int = 3

class ShotPlanned(BaseModel):
    type: Literal["shot_planned"] = "shot_planned"
    shots: list[Shot]

class ImageReady(BaseModel):
    type: Literal["image_ready"] = "image_ready"
    shot_index: int
    image_data_url: str

class LocationFound(BaseModel):
    type: Literal["location_found"] = "location_found"
    scene: str
    locations: list[LocationSuggestion]

class Done(BaseModel):
    type: Literal["done"] = "done"

class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str
