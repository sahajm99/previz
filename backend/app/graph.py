"""The story graph. Typed edges, and the knowledge horizon (design spec §6.4).

`store.Character.knows` is a flat list of facts with a `since_scene`. That covers
the simple question, "what does Maya know by scene 3", and it cannot answer any of
the ones the spec actually asks for: who has met whom, who is lying to whom, who
wants something from whom, and therefore what happens if these two are in a room.

So this module holds the edge table, in the same shape as `story_edges` in spec
§4, and keeps its own store rather than editing `app/store.py`, which several
tabs depend on. It syncs itself from `Character.knows` on first use, so the seeded
story populates the graph with nothing else changing.

Three things read out of here:

  * `horizon(character, scene)` · what a character knows as of scene N, direct
    facts plus everything those facts imply. This goes into the CharacterVoice
    prompt as a hard boundary (§6.3) and it is the whole reason Ravi cannot
    mention the route cut in scene 2.
  * `check_text(character, scene, text)` · the ScriptSupervisor check. Given a
    line, does it refer to something outside that character's horizon.
  * `irony(scene)` and `two_in_a_room(a, b, scene)` · what the audience knows and
    a character does not, and what two people have on each other. Muse reads
    these, which is the graph paying for itself twice.

Why recursion is load bearing and not decoration: a fact can imply another fact.
Learning that the route is cut implies the depot is losing a shift, which implies
someone is being moved. A character who learns the first fact can reason to the
third, and a character who has not learned any of them cannot refer to any. One
edge kind, `implies`, gets that right for every fact at once, rather than making
whoever writes the seed enumerate the closure by hand.
"""
from __future__ import annotations

import re
import threading
import uuid
from dataclasses import asdict, dataclass, field

from app.store import store

# Edge kinds. `knows` and `knows_about` are the horizon (§6.4). The rest are the
# relationship graph Muse reads. `implies` is fact to fact, and is what makes the
# horizon a closure rather than a list.
FACT_KINDS = ("knows", "knows_about", "implies")
RELATION_KINDS = ("met", "wants_from", "lying_to", "owes", "loves", "fears",
                  "works_for", "related_to")
KINDS = FACT_KINDS + RELATION_KINDS

_WORD = re.compile(r"[a-z0-9']+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "it",
         "is", "was", "with", "that", "this", "as", "but", "his", "her", "they",
         "he", "she", "not", "no", "has", "have", "had", "been", "are", "were",
         "will", "would", "there", "their", "them", "she's", "he's", "i'm",
         "you", "your", "my", "me", "we", "us", "out", "up", "from", "by",
         "one", "all", "about", "into", "than", "then", "so", "if", "what"}


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower())
            if len(w) > 3 and w not in _STOP}


@dataclass
class Edge:
    """One typed, scene stamped assertion.

    `dst_text` carries the fact itself when the destination is not an entity, so a
    fact does not need its own table to be pointed at. Facts are identified by
    their text, normalised, which is enough at story scale and keeps the seed
    readable.
    """
    id: str
    story_id: str
    src_type: str                 # character | location | fact | story
    src_id: str                   # entity id, or the normalised fact key
    kind: str
    dst_type: str = "fact"
    dst_id: str = ""
    dst_text: str = ""
    since_scene: int = 1
    layer: str = "canon"          # canon | draft, exactly as §5.1
    note: str = ""
    created_by: str = "user"

    def json(self) -> dict:
        return asdict(self)


def fact_key(text: str) -> str:
    """Facts are keyed by their normalised text, so the same fact asserted by two
    characters is one node rather than two."""
    return " ".join(_WORD.findall(text.lower()))[:120]


class Graph:
    """Edges for every story. Single writer under a lock, like `store.Store`."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.edges: dict[str, Edge] = {}
        self._synced: set[str] = set()

    # -- writes -----------------------------------------------------------
    def add(self, story_id: str, src_type: str, src_id: str, kind: str,
            dst_text: str = "", dst_type: str = "fact", dst_id: str = "",
            since_scene: int = 1, layer: str = "canon", note: str = "",
            created_by: str = "user") -> Edge:
        if kind not in KINDS:
            raise ValueError(f"unknown edge kind: {kind}")
        with self._lock:
            e = Edge(id=_id(), story_id=story_id, src_type=src_type,
                     src_id=src_id, kind=kind, dst_type=dst_type,
                     dst_id=dst_id or (fact_key(dst_text) if dst_text else ""),
                     dst_text=dst_text.strip(), since_scene=since_scene,
                     layer=layer, note=note, created_by=created_by)
            self.edges[e.id] = e
            return e

    def drop(self, edge_id: str) -> bool:
        with self._lock:
            return self.edges.pop(edge_id, None) is not None

    def for_story(self, story_id: str) -> list[Edge]:
        return [e for e in self.edges.values() if e.story_id == story_id]

    # -- sync -------------------------------------------------------------
    def sync_from_store(self, story_id: str, force: bool = False) -> int:
        """Lift `Character.knows` into `knows` edges, then load the seed edges.

        Idempotent, and called on read rather than at startup, so the seed and the
        character builder tab stay the only writers of `knows` and this module
        never has to be in their import order.
        """
        with self._lock:
            if story_id in self._synced and not force:
                return 0
            st = store.story(story_id)
            existing = {(e.src_id, e.dst_id) for e in self.for_story(story_id)
                        if e.kind == "knows"}
            n = 0
            for c in st.characters.values():
                for k in c.knows:
                    text = (k.get("fact") or "").strip()
                    if not text or (c.id, fact_key(text)) in existing:
                        continue
                    self.add(story_id, "character", c.id, "knows", text,
                             since_scene=int(k.get("since_scene", 1)),
                             layer=k.get("layer", "canon"),
                             created_by="seed")
                    n += 1
            self._synced.add(story_id)
            n += self._load_seed_edges(story_id)
            return n

    def _load_seed_edges(self, story_id: str) -> int:
        """Relationships and implications from `data/seed/story_edges.json`.

        Data rather than code, so adding "Ravi is lying to his wife too" is a JSON
        line and not a deploy. Missing or malformed file is not fatal: the graph
        degrades to the `knows` edges it lifted from the store, which is still a
        working horizon.
        """
        from app.store import SEED, load_json

        path = SEED / "story_edges.json"
        if not path.exists():
            return 0
        try:
            data = load_json(path)
        except Exception as exc:                        # noqa: BLE001
            print(f"  graph: seed edges skipped ({type(exc).__name__}: {exc})")
            return 0

        st = store.story(story_id)
        seen = {(e.src_id, e.kind, e.dst_id) for e in self.for_story(story_id)}
        n = 0

        for r in data.get("relations", []):
            src = st.character_by_name(r.get("src", ""))
            if not src:
                continue
            if r.get("dst_fact"):
                key = (src.id, r["kind"], fact_key(r["dst_fact"]))
                if key in seen:
                    continue
                self.add(story_id, "character", src.id, r["kind"],
                         r["dst_fact"], since_scene=int(r.get("since_scene", 1)),
                         note=r.get("note", ""), created_by="seed")
            else:
                dst = st.character_by_name(r.get("dst", ""))
                if not dst:
                    continue
                key = (src.id, r["kind"], dst.id)
                if key in seen:
                    continue
                self.add(story_id, "character", src.id, r["kind"],
                         dst_type="character", dst_id=dst.id,
                         dst_text=dst.name,
                         since_scene=int(r.get("since_scene", 1)),
                         note=r.get("note", ""), created_by="seed")
            seen.add(key)
            n += 1

        for imp in data.get("implies", []):
            src_key = fact_key(imp.get("src_fact", ""))
            if not src_key:
                continue
            key = (src_key, "implies", fact_key(imp.get("dst_fact", "")))
            if key in seen:
                continue
            self.add(story_id, "fact", src_key, "implies",
                     imp.get("dst_fact", ""),
                     since_scene=int(imp.get("since_scene", 1)),
                     created_by="seed")
            seen.add(key)
            n += 1
        return n

    # -- the horizon ------------------------------------------------------
    def horizon(self, story_id: str, character_id: str,
                scene_number: int) -> list[dict]:
        """What this character knows as of scene N. Recursive over `implies`.

        Anything learned later is invisible, which is the point: the boundary is
        what stops a character referring to something they have not been told.
        """
        self.sync_from_store(story_id)
        edges = [e for e in self.for_story(story_id)
                 if e.since_scene <= scene_number]
        direct = [e for e in edges
                  if e.kind in ("knows", "knows_about")
                  and e.src_type == "character" and e.src_id == character_id]

        implies: dict[str, list[Edge]] = {}
        for e in edges:
            if e.kind == "implies":
                implies.setdefault(e.src_id, []).append(e)

        out: dict[str, dict] = {}
        frontier: list[tuple[Edge, int, str]] = [(e, 0, "told") for e in direct]
        while frontier:
            e, depth, how = frontier.pop()
            key = e.dst_id or fact_key(e.dst_text)
            if key in out:
                # Keep the earliest scene, because the earliest is when the
                # boundary actually moved for this character.
                if e.since_scene < out[key]["since_scene"]:
                    out[key]["since_scene"] = e.since_scene
                continue
            out[key] = {"fact": e.dst_text, "key": key, "kind": e.kind,
                        "since_scene": e.since_scene, "layer": e.layer,
                        "via": how, "depth": depth, "edge_id": e.id}
            if depth < 4:
                for imp in implies.get(key, []):
                    frontier.append((imp, depth + 1, f"implied by: {e.dst_text}"))
        return sorted(out.values(),
                      key=lambda f: (f["since_scene"], f["depth"], f["fact"]))

    def horizon_facts(self, story_id: str, character_id: str,
                      scene_number: int) -> list[str]:
        """The horizon as plain strings, which is what `voice.write_exchange`
        wants for its `knows` argument. One call, so no caller has to know the
        shape of an edge."""
        return [f["fact"] for f in
                self.horizon(story_id, character_id, scene_number)]

    def knows_map(self, story_id: str, scene_number: int,
                  character_ids: list[str] | None = None) -> dict[str, list[str]]:
        """`{character_name: [facts]}` for every character in scope, keyed by name
        because that is the contract `voice.write_exchange` already uses."""
        st = store.story(story_id)
        ids = character_ids or list(st.characters)
        return {st.characters[cid].name:
                self.horizon_facts(story_id, cid, scene_number)
                for cid in ids if cid in st.characters}

    # -- what the audience knows and a character does not -----------------
    def irony(self, story_id: str, scene_number: int) -> list[dict]:
        """Dramatic irony as of scene N: every fact established by now, and who
        is still in the dark about it.

        This is the single most useful thing the graph produces for a writer, and
        it is unreachable from a flat per character list.
        """
        self.sync_from_store(story_id)
        st = store.story(story_id)
        established: dict[str, str] = {}
        for e in self.for_story(story_id):
            if e.kind in ("knows", "knows_about") and e.since_scene <= scene_number:
                established.setdefault(e.dst_id or fact_key(e.dst_text),
                                       e.dst_text)
        rows = []
        for key, text in established.items():
            knowers, blind = [], []
            for c in st.characters.values():
                known = {f["key"] for f in
                         self.horizon(story_id, c.id, scene_number)}
                (knowers if key in known else blind).append(c.name)
            if knowers and blind:
                rows.append({"fact": text, "key": key,
                             "known_by": sorted(knowers),
                             "unknown_to": sorted(blind)})
        return rows

    def two_in_a_room(self, story_id: str, a_id: str, b_id: str,
                      scene_number: int) -> dict:
        """What happens if these two are in a room, in facts rather than vibes.

        Returns what each holds that the other does not, plus every edge between
        them. A scene is the difference between two horizons, so this is the
        difference, computed.
        """
        st = store.story(story_id)
        a, b = st.characters[a_id], st.characters[b_id]
        ha = {f["key"]: f["fact"] for f in self.horizon(story_id, a_id, scene_number)}
        hb = {f["key"]: f["fact"] for f in self.horizon(story_id, b_id, scene_number)}
        between = [e.json() for e in self.for_story(story_id)
                   if e.kind in RELATION_KINDS
                   and e.since_scene <= scene_number
                   and {e.src_id, e.dst_id} == {a_id, b_id}]
        return {
            "scene": scene_number,
            "a": {"id": a_id, "name": a.name,
                  "holds_over_b": [ha[k] for k in ha.keys() - hb.keys()]},
            "b": {"id": b_id, "name": b.name,
                  "holds_over_a": [hb[k] for k in hb.keys() - ha.keys()]},
            "shared": [ha[k] for k in ha.keys() & hb.keys()],
            "edges_between": between,
        }

    # -- the ScriptSupervisor check ---------------------------------------
    def check_text(self, story_id: str, character_id: str, scene_number: int,
                   text: str) -> list[dict]:
        """Does this line refer to something outside the character's horizon.

        Lexical on purpose. A model asked "did she just reveal something" answers
        differently on different runs, and a check that is only sometimes right is
        worse than one that is narrow and always right. The rule: a fact known to
        someone else, absent from this character's horizon, whose DISTINCTIVE
        words appear in the line, is a violation.

        Two things make it quiet enough to leave switched on, and both were put
        here because the naive version fired on correct lines:

        **One, a word is only evidence if it is rare in this story.** In The Night
        Route almost every fact contains "night" and "route", so matching on them
        flags any line about the job. Tokens are weighted by how many facts they
        appear in, and a token in more than a third of them is treated as
        background rather than as a reference.

        **Two, a paraphrase of something they already know is not a violation.**
        Maya's copy of a fact reads "Ravi has run the night route for twenty-two
        years" and Ravi's own reads "He has run the night route for twenty-two
        years". Those are one fact and two keys, so a check that compares keys
        alone accuses him of knowing his own history. Candidates whose words are
        mostly contained in something already inside the horizon are dropped.

        Same shape of check as `voice._check_scene_for_secrets`, aimed at one
        character's line rather than at the shared scene brief.
        """
        from collections import Counter

        self.sync_from_store(story_id)
        st = store.story(story_id)
        mine = self.horizon(story_id, character_id, scene_number)
        known_keys = {f["key"] for f in mine}
        known_tokens = [_tokens(f["fact"]) for f in mine if f["fact"]]
        said = _tokens(text)

        candidates = [e for e in self.for_story(story_id)
                      if e.kind in ("knows", "knows_about") and e.dst_text]

        # Document frequency over the distinct facts in this story.
        corpus = {e.dst_id or fact_key(e.dst_text): _tokens(e.dst_text)
                  for e in candidates}
        df = Counter(t for toks in corpus.values() for t in toks)
        ceiling = max(1.0, 0.34 * len(corpus))

        hits = []
        for e in candidates:
            key = e.dst_id or fact_key(e.dst_text)
            if key in known_keys or e.src_id == character_id:
                continue
            distinctive = _tokens(e.dst_text)
            if not distinctive:
                continue
            # Already known under different words.
            if any(len(distinctive & k) / len(distinctive) >= 0.6
                   for k in known_tokens if k):
                continue
            overlap = {t for t in distinctive & said if df[t] <= ceiling}
            # Two rare words in common is a reference, one is a coincidence. A
            # supervisor that cries wolf gets switched off, and then it catches
            # nothing at all.
            if len(overlap) >= 2:
                holder = st.characters.get(e.src_id)
                hits.append({"fact": e.dst_text,
                             "matched": sorted(overlap),
                             "learns_in_scene": e.since_scene,
                             "held_by": holder.name if holder else e.src_id,
                             "held_by_id": e.src_id})
        return hits


graph = Graph()


# --------------------------------------------------------------- into the bible

def reindex_edges(story_id: str) -> int:
    """Put relationship edges into the retrieval index.

    `knows` edges are already chunked by `bible._character_chunks`. Relationships
    are not, and they are exactly what a query like "what does Ravi want from
    Maya" needs to hit. Written through `index.add` rather than by editing
    bible.py, so this stays additive.
    """
    from app.bible import index

    graph.sync_from_store(story_id)
    st = store.story(story_id)
    index.drop_entity(story_id, "edge", None)

    def label(t: str, i: str) -> str:
        if t == "character" and i in st.characters:
            return st.characters[i].name
        if t == "location" and i in st.locations:
            return st.locations[i].name
        return i

    n = 0
    for e in graph.for_story(story_id):
        if e.kind not in RELATION_KINDS:
            continue
        who = label(e.src_type, e.src_id)
        whom = e.dst_text or label(e.dst_type, e.dst_id)
        index.add(story_id, "edge", None,
                  f"{who} · {e.kind.replace('_', ' ')} · {whom}"
                  + (f" · from scene {e.since_scene}" if e.since_scene else "")
                  + (f" · {e.note}" if e.note else ""),
                  layer=e.layer, source_ref=f"story_edges:{e.id}",
                  created_by=e.created_by)
        n += 1
    return n
