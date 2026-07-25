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
from fastapi import APIRouter, Depends

from app.api import auth as auth_routes
from app.api import bible as bible_routes
from app.api import board, cast, health, knowledge, script, scout
from app.auth import current_user

api = APIRouter(prefix="/api")

# OPEN: auth routes, or nobody could ever sign in, and health, because a health
# endpoint behind a login reports a working service as broken to every probe that
# calls it. Everything after these is guarded by one dependency, so no individual
# endpoint has to remember to check.
#
# With GOOGLE_OAUTH_CLIENT_ID unset the dependency returns the local user and the
# whole app behaves exactly as it did before, which is what keeps the boot safe.
api.include_router(auth_routes.router, tags=["auth"])
api.include_router(health.router, tags=["health"])

guarded = [Depends(current_user)]
api.include_router(bible_routes.router, tags=["bible"], dependencies=guarded)
api.include_router(knowledge.router, tags=["knowledge"], dependencies=guarded)
api.include_router(cast.router, tags=["cast"], dependencies=guarded)
api.include_router(board.router, tags=["board"], dependencies=guarded)
api.include_router(script.router, tags=["script"], dependencies=guarded)
api.include_router(scout.router, tags=["scout"], dependencies=guarded)

__all__ = ["api"]
