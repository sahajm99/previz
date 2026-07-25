"""The knowledge layer. Chunks, hybrid retrieval, and the Continuity Pack.

pgvector and Postgres full text are cancelled for today (docs/NOW.md), so this
is numpy cosine over roughly 50 chunks plus a token overlap score. At this size
that is not a compromise: an exhaustive scan of 50 vectors is faster than any
index lookup, and it needs no infrastructure at all.

What is NOT a compromise, and is kept exactly as the spec has it:

  * Hybrid retrieval, because pure vector search fails on the things screenplays
    are made of. "INT. MOTEL ROOM - NIGHT" and "the motel" are the same place but
    are not neighbours in embedding space, and character names demand exact
    matching. Two retrievers, fused by reciprocal rank.
  * One chunk per semantic unit with a human readable prefix naming its entity,
    which makes both lexical matching and model comprehension better than a bare
    fragment would.
  * ONE function assembles context for every model call in the product, with
    fixed slots and fixed budgets, and it reports what it did. That report is
    what the Context tab renders, and it is the difference between diagnosing a
    bad line in five seconds and in twenty minutes.

Invariant one from the spec (§4.1) still holds here: a row and its chunks are
written together, via reindex(). There is no window in which a fact and its index
disagree, because reindex() is called in the same request that wrote the row.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from app.store import Character, Location, Scene, Story, store

_WORD = re.compile(r"[a-z0-9']+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for",
         "it", "is", "was", "with", "that", "this", "as", "but", "his", "her",
         "they", "he", "she", "what", "you", "your", "do", "does", "have", "has"}


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower())
            if len(w) > 2 and w not in _STOP}


@dataclass
class Chunk:
    id: str
    story_id: str
    entity_type: str          # character | scene | shot | location | style | story
    entity_id: str | None
    layer: str                # canon | draft
    text: str
    source_ref: str = ""
    created_by: str = "user"
    embedding: list[float] | None = None
    tokens: set[str] = field(default_factory=set)

    def json(self) -> dict:
        return {"id": self.id, "entity_type": self.entity_type,
                "entity_id": self.entity_id, "layer": self.layer,
                "text": self.text, "source_ref": self.source_ref,
                "created_by": self.created_by}


class Index:
    """Chunks for every story, with lazy embedding.

    Embeddings are filled in a batch by `embed_pending()` rather than per write.
    The spec puts the embedding call inside the write transaction, and that is
    right when the write is a database round trip anyway. Here a write is a dict
    assignment and the embedding is a network call, so batching is the honest
    version of the same guarantee: retrieval degrades to lexical only until the
    batch lands, and never returns a chunk whose text disagrees with its row.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.chunks: dict[str, Chunk] = {}
        self._n = 0

    def add(self, story_id: str, entity_type: str, entity_id: str | None,
            text: str, layer: str = "canon", source_ref: str = "",
            created_by: str = "user") -> Chunk:
        with self._lock:
            self._n += 1
            cid = f"c{self._n:04d}"
            ch = Chunk(id=cid, story_id=story_id, entity_type=entity_type,
                       entity_id=entity_id, layer=layer, text=text.strip(),
                       source_ref=source_ref, created_by=created_by,
                       tokens=_tokens(text))
            self.chunks[cid] = ch
            return ch

    def drop_entity(self, story_id: str, entity_type: str,
                    entity_id: str | None) -> int:
        with self._lock:
            gone = [k for k, c in self.chunks.items()
                    if c.story_id == story_id and c.entity_type == entity_type
                    and c.entity_id == entity_id]
            for k in gone:
                del self.chunks[k]
            return len(gone)

    def for_story(self, story_id: str) -> list[Chunk]:
        return [c for c in self.chunks.values() if c.story_id == story_id]

    def pending(self, story_id: str | None = None) -> list[Chunk]:
        return [c for c in self.chunks.values() if c.embedding is None
                and (story_id is None or c.story_id == story_id)]

    def embed_pending(self, story_id: str | None = None) -> int:
        """Fill in missing embeddings. Safe to call repeatedly and safe to fail:
        a chunk with no embedding is still lexically searchable, so a Vertex
        outage degrades retrieval rather than breaking it.
        """
        todo = self.pending(story_id)
        if not todo:
            return 0
        try:
            from app.voice import embed_text
            vecs = embed_text([c.text[:2000] for c in todo])
        except Exception as exc:                       # noqa: BLE001
            print(f"  bible: embedding skipped ({type(exc).__name__}: {exc})")
            return 0
        for c, v in zip(todo, vecs):
            c.embedding = v
        return len(todo)

    # ------------------------------------------------------------- retrieval
    def search(self, story_id: str, query: str, k: int = 8,
               layers: tuple[str, ...] = ("canon", "draft")) -> list[dict]:
        """Hybrid: cosine and token overlap, fused by reciprocal rank (k=60).

        Canon is boosted over draft, so a confirmed fact outranks a guess at the
        same relevance.
        """
        pool = [c for c in self.for_story(story_id) if c.layer in layers]
        if not pool or not query.strip():
            return []
        qt = _tokens(query)

        lex = sorted(pool, key=lambda c: -_overlap(qt, c.tokens))
        lex_rank = {c.id: i for i, c in enumerate(lex)
                    if _overlap(qt, c.tokens) > 0}

        vec_rank: dict[str, int] = {}
        vectors = [c for c in pool if c.embedding is not None]
        if vectors:
            try:
                from app.voice import embed_text
                import numpy as np
                qv = np.asarray(embed_text([query])[0], dtype="float32")
                M = np.asarray([c.embedding for c in vectors], dtype="float32")
                sims = (M @ qv) / (
                    np.linalg.norm(M, axis=1) * np.linalg.norm(qv) + 1e-9)
                order = np.argsort(-sims)
                vec_rank = {vectors[int(i)].id: r for r, i in enumerate(order)}
            except Exception as exc:                   # noqa: BLE001
                print(f"  bible: vector leg skipped ({type(exc).__name__})")

        RRF = 60.0
        scored: list[tuple[float, Chunk]] = []
        for c in pool:
            s = 0.0
            if c.id in lex_rank:
                s += 1 / (RRF + lex_rank[c.id])
            if c.id in vec_rank:
                s += 1 / (RRF + vec_rank[c.id])
            if s == 0:
                continue
            if c.layer == "canon":
                s *= 1.15
            scored.append((s, c))
        scored.sort(key=lambda t: -t[0])
        return [{**c.json(), "score": round(s, 6)} for s, c in scored[:k]]


def _overlap(q: set[str], t: set[str]) -> float:
    if not q or not t:
        return 0.0
    return len(q & t) / len(q)


index = Index()


# ----------------------------------------------------------------- reindexing

def _character_chunks(story_id: str, c: Character) -> None:
    index.drop_entity(story_id, "character", c.id)
    if c.identity_card:
        index.add(story_id, "character", c.id,
                  f"{c.name} · how she looks · "
                  f"{c.identity_card.get('descriptor','')} "
                  f"Wardrobe: {c.identity_card.get('wardrobe','')}",
                  source_ref=f"identity_card:{c.id}")
    if c.voice_card:
        vc = c.voice_card
        index.add(story_id, "character", c.id,
                  f"{c.name} · how she speaks · {vc.get('card','')} "
                  f"Says: {'; '.join(vc.get('phrases', []))}. "
                  f"Never says: {'; '.join(vc.get('never_says', []))}",
                  source_ref=f"voice_card:{c.id}")
    # One chunk per answered question, prefixed with the entity name.
    for q, a in c.answers.items():
        if a and a.strip():
            index.add(story_id, "character", c.id,
                      f"{c.name} · {q.rstrip('?')} · {a}",
                      source_ref=f"character_answers:{c.id}")
    for k in c.knows:
        index.add(story_id, "character", c.id,
                  f"{c.name} knows from scene {k.get('since_scene', 0)} · "
                  f"{k.get('fact','')}",
                  layer=k.get("layer", "canon"),
                  source_ref=f"story_edges:{c.id}")


def _scene_chunks(story_id: str, s: Scene) -> None:
    index.drop_entity(story_id, "scene", s.id)
    index.add(story_id, "scene", s.id,
              f"Scene {s.number} · {s.slugline} · {s.synopsis}",
              source_ref=f"scenes:{s.id}")
    if s.body.strip():
        lines = s.body.splitlines()
        for i in range(0, len(lines), 40):
            index.add(story_id, "scene", s.id,
                      f"Scene {s.number} · {s.slugline} · text · "
                      + "\n".join(lines[i:i + 40]),
                      source_ref=f"scenes:{s.id}")


def _location_chunks(story_id: str, l: Location) -> None:
    index.drop_entity(story_id, "location", l.id)
    index.add(story_id, "location", l.id,
              f"Location · {l.name} · {l.address} · {l.notes}",
              layer="canon" if l.shortlisted else "draft",
              source_ref=f"locations:{l.id}")


def reindex_entity(story_id: str, entity_type: str, entity_id: str) -> None:
    """Called in the same request that wrote the row. Invariant one, §4.1."""
    st = store.story(story_id)
    if entity_type == "character" and entity_id in st.characters:
        _character_chunks(story_id, st.characters[entity_id])
    elif entity_type == "scene" and entity_id in st.scenes:
        _scene_chunks(story_id, st.scenes[entity_id])
    elif entity_type == "location" and entity_id in st.locations:
        _location_chunks(story_id, st.locations[entity_id])


def reindex_story(story_id: str) -> int:
    st = store.story(story_id)
    index.drop_entity(story_id, "story", None)
    index.drop_entity(story_id, "style", None)
    index.add(story_id, "story", None,
              f"{st.title} · logline · {st.logline}. {st.summary}",
              source_ref="stories")
    for axis, val in (st.style or {}).items():
        index.add(story_id, "style", None, f"Visual style · {axis} · {val}",
                  source_ref="style_cards")
    for c in st.characters.values():
        _character_chunks(story_id, c)
    for s in st.scenes.values():
        _scene_chunks(story_id, s)
    for l in st.locations.values():
        _location_chunks(story_id, l)
    return len(index.for_story(story_id))


# ------------------------------------------------------------ Continuity Pack

BUDGETS = {"style": 400, "spine": 700, "cast": 300, "retrieved": 800,
           "local": 600, "turn": 600}
# Dropped in this order on overflow. style and cast are never dropped: they are
# the identity, and an overflowing pack that drops identity is exactly the drift
# this file exists to prevent.
DROP_ORDER = ("turn", "local", "retrieved")


@dataclass
class Pack:
    slots: dict[str, str]
    sizes: dict[str, int]
    chunk_ids: list[str]
    dropped: list[str] = field(default_factory=list)

    def text(self) -> str:
        order = ("style", "spine", "cast", "retrieved", "local", "turn")
        return "\n\n".join(f"## {k.upper()}\n{self.slots[k]}"
                           for k in order if self.slots.get(k))

    def report(self) -> dict:
        return {"slots": self.sizes, "chunk_ids": self.chunk_ids,
                "dropped": self.dropped,
                "total_chars": sum(self.sizes.values())}


def build_pack(story_id: str, query: str = "",
               character_ids: list[str] | None = None,
               scene_number: int | None = None,
               turn: str = "", total_budget: int = 3400) -> Pack:
    """The one context assembler. Every model call in the product goes through
    here, so there is exactly one place where a bad context is diagnosed.
    """
    st = store.story(story_id)
    slots: dict[str, str] = {}
    chunk_ids: list[str] = []

    slots["style"] = ("\n".join(f"{k}: {v}" for k, v in (st.style or {}).items())
                      or "no style card yet")[:BUDGETS["style"]]

    spine = [f"TITLE: {st.title}", f"LOGLINE: {st.logline}"]
    if st.summary:
        spine.append(f"SO FAR: {st.summary}")
    spine.append("SCENES:")
    spine += [f"  {s['number']}. {s['slugline']} — {s['synopsis']}"
              for s in st.scene_index()]
    slots["spine"] = "\n".join(spine)[:BUDGETS["spine"]]

    cast_blocks = []
    for cid in (character_ids or list(st.characters)):
        c = st.characters.get(cid)
        if not c:
            continue
        b = [f"{c.name} ({c.role})"]
        if c.identity_card:
            b.append(f"  looks: {c.identity_card.get('descriptor','')}")
        if c.voice_card:
            b.append(f"  speaks: {c.voice_card.get('card','')}")
        if scene_number is not None:
            known = c.knows_by(scene_number)
            if known:
                b.append("  knows: " + "; ".join(known))
        cast_blocks.append("\n".join(b)[:BUDGETS["cast"]])
    slots["cast"] = "\n".join(cast_blocks)

    hits = index.search(story_id, query, k=8) if query else []
    got, used = [], 0
    for h in hits:
        if used + len(h["text"]) > BUDGETS["retrieved"]:
            break
        got.append(f"[{h['layer']}] {h['text']}")
        chunk_ids.append(h["id"])
        used += len(h["text"])
    slots["retrieved"] = "\n".join(got)

    local = ""
    if scene_number is not None:
        sc = st.scene_by_number(scene_number)
        prev = st.scene_by_number(scene_number - 1)
        if prev and prev.body:
            local += f"END OF SCENE {prev.number}:\n{prev.body[-400:]}\n\n"
        if sc and sc.body:
            local += f"SCENE {sc.number} SO FAR:\n{sc.body[-400:]}"
    slots["local"] = local[:BUDGETS["local"]]
    slots["turn"] = turn[-BUDGETS["turn"]:] if turn else ""

    dropped: list[str] = []
    sizes = {k: len(v) for k, v in slots.items()}
    for name in DROP_ORDER:
        if sum(sizes.values()) <= total_budget:
            break
        if slots.get(name):
            slots[name] = ""
            sizes[name] = 0
            dropped.append(name)
    return Pack(slots=slots, sizes=sizes, chunk_ids=chunk_ids, dropped=dropped)
