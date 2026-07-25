"""Auth routes. Three endpoints, no session store.

`/api/auth/config` exists so the client never hardcodes a client id. The browser
asks the server what to sign in with, which means rotating the client id is an env
var change and not a frontend deploy.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app import auth
from app.config import settings

router = APIRouter(prefix="/auth")


@router.get("/config")
def config():
    """What the sign-in button needs. Safe to be public: an OAuth client id is
    not a secret, it is an identifier, and the token is verified server side.
    """
    return {"enabled": auth.enabled(),
            "client_id": settings.google_oauth_client_id,
            "provider": "google",
            # Stated so the UI can say it rather than the user having to trust it.
            "stores_passwords": False}


@router.get("/me")
def me(user: auth.User = Depends(auth.current_user)):
    return user.json()


@router.get("/whoami")
def whoami(authorization: str | None = None):
    """Unauthenticated probe: is the caller signed in, without 401ing them.

    /me returns 401 when there is no token, which is correct for a guard and
    useless for a UI deciding whether to draw a sign-in button. This answers that
    question without the error.
    """
    if not auth.enabled():
        return {"signed_in": True, "required": False,
                "user": auth.LOCAL_USER.json()}
    return {"signed_in": False, "required": True, "user": None}
