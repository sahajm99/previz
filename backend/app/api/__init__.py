"""HTTP surface. One router per surface, so the four tabs never touch each
other's files.

Ownership, from docs/NOW.md:

    bible.py   knowledge base + Canon strip        Sampreeth
    cast.py    character builder + the 100 Qs      kk
    board.py   storyboard + the face referee       Sahaj
    script.py  dialogue + the voice referee        Sampreeth
    scout.py   locations                           gaurav

Every router reads and writes through `app.store` and reindexes through
`app.bible`. Nothing holds its own state, so no two tabs can disagree about what
is true.
"""
from fastapi import APIRouter

from app.api import bible as bible_routes
from app.api import board, cast, knowledge, script, scout

api = APIRouter(prefix="/api")
api.include_router(bible_routes.router, tags=["bible"])
api.include_router(knowledge.router, tags=["knowledge"])
api.include_router(cast.router, tags=["cast"])
api.include_router(board.router, tags=["board"])
api.include_router(script.router, tags=["script"])
api.include_router(scout.router, tags=["scout"])

__all__ = ["api"]
