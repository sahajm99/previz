"""The horizon and the supervisor check, on the seeded story.

Written against the seed rather than against fixtures on purpose: the seed is what
the demo runs, so a test that passes on invented data and fails on The Night Route
would tell us nothing at the only moment that counts.
"""
from __future__ import annotations

import pytest

from app import seed
from app.graph import graph
from app.store import store


@pytest.fixture(scope="module")
def sid() -> str:
    if not store.stories:
        seed.build()
    s = store.default_story_id
    assert s
    graph.sync_from_store(s, force=True)
    return s


def _c(sid: str, name: str) -> str:
    c = store.story(sid).character_by_name(name)
    assert c, f"no character {name}"
    return c.id


# ------------------------------------------------------------------- the horizon

def test_ravi_does_not_know_the_cut_before_scene_three(sid):
    """The whole point of §6.4. He learns it in scene 3, so it is invisible in 2."""
    early = graph.horizon_facts(sid, _c(sid, "Ravi"), 2)
    assert not any("being cut" in f for f in early)

    late = graph.horizon_facts(sid, _c(sid, "Ravi"), 3)
    assert any("being cut" in f for f in late)


def test_maya_knows_it_from_scene_one(sid):
    assert any("being cut" in f
               for f in graph.horizon_facts(sid, _c(sid, "Maya"), 1))


def test_horizon_only_grows(sid):
    """A later scene can never know less. If this fails, a `since_scene` is wrong
    somewhere and every downstream prompt is quietly built on it."""
    for name in ("Maya", "Ravi"):
        cid = _c(sid, name)
        sizes = [len(graph.horizon_facts(sid, cid, n)) for n in (1, 2, 3, 4)]
        assert sizes == sorted(sizes), (name, sizes)


def test_implication_closure_is_reached(sid):
    """Maya is told the route is cut, and can therefore reason two steps to who it
    costs. Depth is what separates this from a flat list."""
    facts = graph.horizon(sid, _c(sid, "Maya"), 1)
    assert any(f["depth"] >= 2 for f in facts), [f["depth"] for f in facts]
    inferred = [f for f in facts if f["depth"] > 0]
    assert all(f["via"].startswith("implied by") for f in inferred)


def test_inference_inherits_the_boundary(sid):
    """Ravi must not reach an implication of a fact he has not been told yet."""
    at2 = graph.horizon(sid, _c(sid, "Ravi"), 2)
    assert not any("moved off it" in f["fact"] for f in at2)
    at3 = graph.horizon(sid, _c(sid, "Ravi"), 3)
    assert any("moved off it" in f["fact"] for f in at3)


# --------------------------------------------------------- the supervisor check

LINE = "So the night route is being cut at the end of the month, then."


def test_line_is_a_violation_before_he_learns_it(sid):
    hits = graph.check_text(sid, _c(sid, "Ravi"), 2, LINE)
    assert hits, "Ravi referring to the cut in scene 2 must be caught"
    assert any("being cut" in h["fact"] for h in hits)
    assert hits[0]["held_by"] == "Maya Raghavan"


def test_same_line_is_fine_once_he_knows(sid):
    """The regression that matters. Two things used to flag this line at scene 3:
    a paraphrase of his own history held under Maya's wording, and matching on
    "night" and "route", which appear in nearly every fact in this story."""
    assert graph.check_text(sid, _c(sid, "Ravi"), 3, LINE) == []


def test_a_line_about_the_job_is_not_a_violation(sid):
    """Background words must not fire. This line refers to nothing withheld."""
    line = "Twenty-two years on this route and not one accident. Ask anyone."
    assert graph.check_text(sid, _c(sid, "Ravi"), 1, line) == []


def test_maya_referring_to_her_own_secret_is_never_a_violation(sid):
    line = "I was on shift. I did not answer the phone."
    assert graph.check_text(sid, _c(sid, "Maya"), 1, line) == []


# ------------------------------------------------------------ irony and the room

def test_irony_names_who_is_in_the_dark(sid):
    gaps = graph.irony(sid, 1)
    cut = next(g for g in gaps if "being cut" in g["fact"])
    assert cut["known_by"] == ["Maya Raghavan"]
    assert cut["unknown_to"] == ["Ravi Menon"]


def test_the_gap_closes_by_scene_three(sid):
    gaps = graph.irony(sid, 3)
    assert not any("being cut" in g["fact"] for g in gaps), \
        "once both know it, it is no longer irony"


def test_two_in_a_room_is_the_difference_between_horizons(sid):
    r = graph.two_in_a_room(sid, _c(sid, "Maya"), _c(sid, "Ravi"), 1)
    assert any("being cut" in f for f in r["a"]["holds_over_b"])
    assert not any("being cut" in f for f in r["b"]["holds_over_a"])
    assert {e["kind"] for e in r["edges_between"]} >= {"met", "lying_to"}


def test_relationship_edges_are_retrievable(sid):
    """Invariant one, §4.1: an edge nobody can retrieve is an edge no agent uses."""
    from app.bible import index
    from app.graph import reindex_edges

    assert reindex_edges(sid) > 0
    hits = index.search(sid, "who is lying to whom", k=8)
    assert any("lying" in h["text"] for h in hits), [h["text"] for h in hits]


def test_knows_map_matches_the_voice_contract(sid):
    """`voice.write_exchange` takes `{name: [facts]}`. Hand it over unreshaped."""
    m = graph.knows_map(sid, 3)
    assert set(m) == {"Maya Raghavan", "Ravi Menon"}
    assert all(isinstance(v, list) and all(isinstance(f, str) for f in v)
               for v in m.values())
