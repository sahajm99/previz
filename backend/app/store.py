"""The store. One process, in memory, plus a JSON seed on disk.

Cloud SQL is cancelled for today (docs/NOW.md), so this is the whole persistence
layer. It is deliberately the same SHAPE as the Postgres schema in the design
spec §4, so swapping it for real tables later is a rewrite of this file only and
nothing above it.

Everything is keyed by string id. Every mutation goes through `Store`, so there
is exactly one place that changes state and exactly one place to add a database.

Compiled cards live here too. That matters: the whole consistency mechanism (§6)
depends on a card being written ONCE and reused verbatim, so the card has to
outlive the request that compiled it. A card recompiled per request would be a
different card every time, which is the drift this product exists to prevent.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve()
# In the repo, data/ sits beside backend/. In the container the layout is flatter,
# so it sits beside app/. Resolved by looking rather than assumed, because getting
# it wrong makes questions.py raise at import, which main.py catches, which means
# the app boots and serves the UI and then 404s every /api route. A quiet failure
# is worth three lines to prevent.
DATA = next((p for p in (_HERE.parents[2] / "data", _HERE.parents[1] / "data")
             if p.is_dir()), _HERE.parents[2] / "data")
SEED = DATA / "seed"
CACHE = _HERE.parents[1] / "demo_cache"


def _id() -> str:
    return uuid.uuid4().hex[:12]


# --------------------------------------------------------------------- entities

@dataclass
class Character:
    id: str
    name: str
    role: str = "supporting"
    aliases: list[str] = field(default_factory=list)
    # {question_text: answer}. This is the contract between the character builder
    # tab and both card compilers. Nothing else is needed to derive a character.
    answers: dict[str, str] = field(default_factory=dict)
    canon_version: int = 1
    # Compiled artifacts, stored as plain dicts so they serialise straight to the
    # client. The dataclasses in consistency.py / voice.py are the runtime form.
    identity_card: dict | None = None
    voice_card: dict | None = None
    sheet_url: str | None = None
    face_score_history: list[float] = field(default_factory=list)
    # What this character knows, by scene number. The knowledge horizon (§6.4).
    knows: list[dict] = field(default_factory=list)

    @property
    def core_answered(self) -> int:
        from app.questions import CORE_TEXTS
        return sum(1 for t in CORE_TEXTS if self.answers.get(t))

    @property
    def completeness(self) -> float:
        return round(len(self.answers) / 100, 3)

    def knows_by(self, scene_number: int) -> list[str]:
        """What they know as of scene N. Anything learned later is invisible."""
        return [k["fact"] for k in self.knows
                if k.get("since_scene", 0) <= scene_number]


@dataclass
class Shot:
    id: str
    scene_id: str
    number: int
    description: str
    shot_size: str = "MS"
    angle: str = "eye"
    lens: str = "35mm"
    movement: str = "static"
    subject: str = ""
    characters: list[str] = field(default_factory=list)   # character ids
    style_preset: str = "realistic"
    image_url: str | None = None
    face_scores: dict[str, float | None] = field(default_factory=dict)
    attempts: int = 0
    status: str = "planned"      # planned | generating | ready | flagged | failed


@dataclass
class Scene:
    id: str
    number: int
    slugline: str
    synopsis: str = ""
    body: str = ""
    int_ext: str = "INT"
    time_of_day: str = "DAY"
    status: str = "draft"
    characters: list[str] = field(default_factory=list)
    location_ids: list[str] = field(default_factory=list)
    shots: list[Shot] = field(default_factory=list)
    # Per character, for this scene: wardrobe / physical / emotional overrides.
    continuity: dict[str, dict] = field(default_factory=dict)


@dataclass
class Location:
    id: str
    name: str
    address: str = ""
    lat: float = 0.0
    lng: float = 0.0
    maps_url: str = ""
    photos: list[dict] = field(default_factory=list)
    notes: str = ""
    shortlisted: bool = False
    attribution: str = ""
    budget_tier: str = "Low"
    permit_status: str = "Required"
    vibe_match_score: float | None = None
    vibe_reasoning: str | None = None
    street_view_url: str | None = None
    embedding: list[float] | None = None
    similar_place_ids: list[str] = field(default_factory=list)
    attached_scenes: list[int] = field(default_factory=list)


@dataclass
class Proposal:
    """Agents write here, never to canon. §5.1."""
    id: str
    entity_type: str
    entity_id: str | None
    field: str
    proposed: Any
    rationale: str
    source_agent: str
    status: str = "pending"


@dataclass
class Story:
    id: str
    title: str
    logline: str = ""
    summary: str = ""
    format: str = "short"
    style: dict = field(default_factory=dict)
    characters: dict[str, Character] = field(default_factory=dict)
    scenes: dict[str, Scene] = field(default_factory=dict)
    locations: dict[str, Location] = field(default_factory=dict)
    proposals: dict[str, Proposal] = field(default_factory=dict)

    def scene_by_number(self, n: int) -> Scene | None:
        return next((s for s in self.scenes.values() if s.number == n), None)

    def character_by_name(self, name: str) -> Character | None:
        low = name.strip().lower()
        for c in self.characters.values():
            if c.name.lower() == low or low in [a.lower() for a in c.aliases]:
                return c
        # Fall back to first-name match, because a shot list says "Maya" and the
        # character is "Maya Raghavan".
        return next((c for c in self.characters.values()
                     if c.name.lower().split()[0] == low.split()[0]), None)

    def scene_index(self) -> list[dict]:
        return [{"number": s.number, "slugline": s.slugline,
                 "synopsis": s.synopsis, "status": s.status,
                 "shots": len(s.shots)}
                for s in sorted(self.scenes.values(), key=lambda s: s.number)]


# ------------------------------------------------------------------- the store

class Store:
    """Single writer, guarded by a lock. Uvicorn runs handlers in a threadpool."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.stories: dict[str, Story] = {}
        self.default_story_id: str | None = None

    # -- stories ----------------------------------------------------------
    def create_story(self, title: str, logline: str = "", **kw) -> Story:
        with self._lock:
            s = Story(id=_id(), title=title, logline=logline, **kw)
            self.stories[s.id] = s
            if self.default_story_id is None:
                self.default_story_id = s.id
            return s

    def story(self, story_id: str | None = None) -> Story:
        sid = story_id or self.default_story_id
        if sid is None or sid not in self.stories:
            raise KeyError(f"no such story: {story_id}")
        return self.stories[sid]

    # -- characters -------------------------------------------------------
    def add_character(self, story_id: str, name: str, role: str = "supporting",
                      answers: dict[str, str] | None = None,
                      knows: list[dict] | None = None) -> Character:
        with self._lock:
            st = self.story(story_id)
            c = Character(id=_id(), name=name, role=role,
                          answers=answers or {}, knows=knows or [])
            st.characters[c.id] = c
            return c

    def set_answers(self, story_id: str, char_id: str,
                    answers: dict[str, str]) -> Character:
        """Merge answers. Any change to canon bumps canon_version and stales the
        compiled cards, which is invariant two from the spec §4.1. Without this a
        corrected fact would keep producing the old face and the old voice.
        """
        with self._lock:
            c = self.story(story_id).characters[char_id]
            changed = {k: v for k, v in answers.items() if c.answers.get(k) != v}
            if not changed:
                return c
            c.answers.update(changed)
            c.canon_version += 1
            c.identity_card = None      # stale: recompile on next use
            c.voice_card = None
            return c

    # -- scenes and shots -------------------------------------------------
    def add_scene(self, story_id: str, number: int, slugline: str, **kw) -> Scene:
        with self._lock:
            st = self.story(story_id)
            sc = Scene(id=_id(), number=number, slugline=slugline, **kw)
            st.scenes[sc.id] = sc
            return sc

    def add_shot(self, story_id: str, scene_id: str, description: str,
                 **kw) -> Shot:
        with self._lock:
            sc = self.story(story_id).scenes[scene_id]
            sh = Shot(id=_id(), scene_id=scene_id, number=len(sc.shots) + 1,
                      description=description, **kw)
            sc.shots.append(sh)
            return sh

    def shot(self, story_id: str, shot_id: str) -> tuple[Scene, Shot]:
        st = self.story(story_id)
        for sc in st.scenes.values():
            for sh in sc.shots:
                if sh.id == shot_id:
                    return sc, sh
        raise KeyError(f"no such shot: {shot_id}")

    # -- locations --------------------------------------------------------
    def add_location(self, story_id: str, **kw) -> Location:
        with self._lock:
            st = self.story(story_id)
            existing = next((l for l in st.locations.values()
                             if l.name == kw.get("name")), None)
            if existing:
                return existing
            loc = Location(id=_id(), **kw)
            st.locations[loc.id] = loc
            return loc

    # -- proposals --------------------------------------------------------
    def propose(self, story_id: str, entity_type: str, entity_id: str | None,
                field_: str, proposed: Any, rationale: str,
                agent: str) -> Proposal:
        with self._lock:
            st = self.story(story_id)
            p = Proposal(id=_id(), entity_type=entity_type, entity_id=entity_id,
                         field=field_, proposed=proposed, rationale=rationale,
                         source_agent=agent)
            st.proposals[p.id] = p
            return p


store = Store()


# ----------------------------------------------------------------- serialisation

def character_json(c: Character) -> dict:
    d = asdict(c)
    d["completeness"] = c.completeness
    d["core_answered"] = c.core_answered
    d["ready_for_dialogue"] = c.core_answered >= 12
    d["has_identity_card"] = c.identity_card is not None
    d["has_voice_card"] = c.voice_card is not None
    d["answer_count"] = len(c.answers)
    return d


def scene_json(s: Scene) -> dict:
    return asdict(s)


def story_json(st: Story, deep: bool = True) -> dict:
    d = {"id": st.id, "title": st.title, "logline": st.logline,
         "summary": st.summary, "format": st.format, "style": st.style,
         "scene_index": st.scene_index(),
         "counts": {"characters": len(st.characters), "scenes": len(st.scenes),
                    "locations": len(st.locations),
                    "shots": sum(len(s.shots) for s in st.scenes.values()),
                    "pending_proposals": sum(
                        1 for p in st.proposals.values()
                        if p.status == "pending")}}
    if deep:
        d["characters"] = [character_json(c) for c in st.characters.values()]
        d["scenes"] = [scene_json(s) for s in
                       sorted(st.scenes.values(), key=lambda s: s.number)]
        d["locations"] = [asdict(l) for l in st.locations.values()]
        d["proposals"] = [asdict(p) for p in st.proposals.values()]
    return d


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
