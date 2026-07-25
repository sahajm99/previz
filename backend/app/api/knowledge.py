"""Knowledge: the story graph, the horizon, and the continuity checks over it.

Sits beside `api/bible.py`, which owns chunks, hybrid search and the Continuity
Pack. Split that way because they answer different questions. The bible answers
"what is written down about this story". This answers "who knows it, since when,
and who is still in the dark", which is the half that keeps dialogue honest.

Every character argument accepts a name or an id, because a shot list and a
storyboard both say "Maya" and only the store says `4f9c1a...`.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.graph import KINDS, RELATION_KINDS, graph, reindex_edges
from app.store import store

router = APIRouter()
STATIC = Path(__file__).resolve().parents[1] / "static"


def _sid(story_id: str | None) -> str:
    try:
        return store.story(story_id).id
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def _char(sid: str, who: str):
    """Resolve a character by id, full name, alias, or first name."""
    st = store.story(sid)
    if who in st.characters:
        return st.characters[who]
    c = st.character_by_name(who)
    if not c:
        raise HTTPException(404, f"no such character: {who}")
    return c


@router.get("/knowledge/graph")
def get_graph(story_id: str | None = None, scene: int | None = None):
    """Every node and edge, ready to draw.

    `scene` filters to what has been established by then, which is what makes the
    graph a timeline rather than a static diagram: drag the scene number and watch
    the edges appear.
    """
    sid = _sid(story_id)
    graph.sync_from_store(sid)
    st = store.story(sid)
    edges = [e for e in graph.for_story(sid)
             if scene is None or e.since_scene <= scene]

    nodes = [{"id": c.id, "type": "character", "label": c.name,
              "role": c.role, "ready": c.core_answered >= 12}
             for c in st.characters.values()]
    facts: dict[str, dict] = {}
    for e in edges:
        if e.dst_type == "fact" and e.dst_text:
            facts.setdefault(e.dst_id, {"id": e.dst_id, "type": "fact",
                                        "label": e.dst_text})
        if e.src_type == "fact":
            facts.setdefault(e.src_id, {"id": e.src_id, "type": "fact",
                                        "label": e.src_id})
    return {"story_id": sid, "scene": scene,
            "nodes": nodes + list(facts.values()),
            "edges": [e.json() for e in edges],
            "kinds": list(KINDS),
            "max_scene": max((s.number for s in st.scenes.values()), default=1)}


@router.get("/knowledge/horizon")
def get_horizon(character: str, scene: int, story_id: str | None = None):
    """What this character knows as of this scene, with provenance.

    `via` says whether they were told a fact or reasoned to it, and `depth` says
    how far. A writer looking at a line that feels wrong wants exactly this.
    """
    sid = _sid(story_id)
    c = _char(sid, character)
    facts = graph.horizon(sid, c.id, scene)
    return {"character": c.name, "character_id": c.id, "scene": scene,
            "count": len(facts),
            "told": [f for f in facts if f["depth"] == 0],
            "inferred": [f for f in facts if f["depth"] > 0],
            "facts": facts}


@router.get("/knowledge/knows")
def get_knows_map(scene: int, story_id: str | None = None,
                  character_ids: str = ""):
    """`{name: [facts]}` for a scene. This is the argument `voice.write_exchange`
    takes, so the script tab can hand it straight over without reshaping it."""
    sid = _sid(story_id)
    ids = [i for i in character_ids.split(",") if i]
    return {"scene": scene, "knows": graph.knows_map(sid, scene, ids or None)}


@router.get("/knowledge/irony")
def get_irony(scene: int, story_id: str | None = None):
    """Every established fact and who is still in the dark about it.

    The list a writer actually works from, and it is not derivable from a single
    character's fact list.
    """
    sid = _sid(story_id)
    return {"scene": scene, "gaps": graph.irony(sid, scene)}


@router.get("/knowledge/room")
def get_room(a: str, b: str, scene: int, story_id: str | None = None):
    """What happens if these two are in a room, as the difference between their
    two horizons plus whatever is already between them."""
    sid = _sid(story_id)
    ca, cb = _char(sid, a), _char(sid, b)
    return graph.two_in_a_room(sid, ca.id, cb.id, scene)


class CheckIn(BaseModel):
    character: str
    scene: int
    text: str


@router.post("/knowledge/check")
def check(body: CheckIn, story_id: str | None = None):
    """The ScriptSupervisor knowledge check, on one line or one whole scene.

    Returns violations rather than a verdict, because the writer decides. A check
    that silently rewrites is a check nobody trusts.
    """
    sid = _sid(story_id)
    c = _char(sid, body.character)
    hits = graph.check_text(sid, c.id, body.scene, body.text)
    return {"character": c.name, "scene": body.scene, "ok": not hits,
            "violations": hits}


class EdgeIn(BaseModel):
    src: str                       # character name or id, or a fact for `implies`
    kind: str
    dst: str = ""                  # character name or id
    dst_fact: str = ""             # or a fact, when the target is not an entity
    since_scene: int = 1
    note: str = ""
    layer: str = "canon"


@router.post("/knowledge/edges")
def add_edge(body: EdgeIn, story_id: str | None = None):
    """Add an edge. Anything a person types is canon immediately (§5.1).

    The approval queue in `api/bible.py` exists for what an agent inferred, not
    for what the user typed, so there is deliberately no proposal step here.
    """
    sid = _sid(story_id)
    if body.kind not in KINDS:
        raise HTTPException(400, f"unknown kind: {body.kind}. one of {KINDS}")

    if body.kind == "implies":
        from app.graph import fact_key
        if not (body.src and body.dst_fact):
            raise HTTPException(400, "implies needs src fact text and dst_fact")
        e = graph.add(sid, "fact", fact_key(body.src), "implies", body.dst_fact,
                      since_scene=body.since_scene, layer=body.layer,
                      note=body.note, created_by="user")
    else:
        src = _char(sid, body.src)
        if body.dst_fact:
            e = graph.add(sid, "character", src.id, body.kind, body.dst_fact,
                          since_scene=body.since_scene, layer=body.layer,
                          note=body.note, created_by="user")
        else:
            dst = _char(sid, body.dst)
            e = graph.add(sid, "character", src.id, body.kind,
                          dst_type="character", dst_id=dst.id,
                          dst_text=dst.name, since_scene=body.since_scene,
                          layer=body.layer, note=body.note, created_by="user")

    # Invariant one, §4.1: the row and its chunks are written together. A
    # relationship that is not retrievable is a relationship no agent will use.
    if body.kind in RELATION_KINDS:
        reindex_edges(sid)
    return e.json()


@router.delete("/knowledge/edges/{edge_id}")
def delete_edge(edge_id: str, story_id: str | None = None):
    sid = _sid(story_id)
    if not graph.drop(edge_id):
        raise HTTPException(404, "no such edge")
    reindex_edges(sid)
    return {"deleted": edge_id}


@router.post("/knowledge/reindex")
def reindex(story_id: str | None = None):
    """Rebuild the edge chunks. Called after an import or a bulk edit."""
    sid = _sid(story_id)
    return {"edge_chunks": reindex_edges(sid),
            "edges": len(graph.for_story(sid))}


@router.get("/knowledge/ui")
def ui():
    """The knowledge console. Served from this router rather than a static mount,
    so it cannot collide with another tab's front end work."""
    p = STATIC / "knowledge.html"
    if not p.exists():
        raise HTTPException(404, "knowledge.html not built yet")
    return FileResponse(p)
