"""Google Sign-In. No passwords, no password storage, no session table.

The whole mechanism:

  1. The browser gets an ID token from Google Identity Services.
  2. It sends that token as `Authorization: Bearer <token>` on every API call.
  3. This module verifies the signature and the audience against Google's public
     keys and pulls the `sub` claim out.

`sub` is the user id. **Never the email**, because an email can be reassigned and
a `sub` cannot, so an email as a primary key is a silent account takeover waiting
to happen.

There is no session to store and nothing to forge: the token is signed by Google
and verified on every request. That is why there is no password, no session table
and no cookie to steal. The cost is one signature check per call, and the keys are
cached by the library.

If `GOOGLE_OAUTH_CLIENT_ID` is not set, auth is DISABLED and every request runs as
a single local user. That is deliberate: the app has to boot and demo without a
console round trip, and a half configured login must not be the reason nothing
works.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from fastapi import Header, HTTPException

from app.config import settings

# Anyone with a Google account can sign in. Restricting by domain would be one
# line here (checking the `hd` claim), and is deliberately not done.
_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

LOCAL_UID = "local"


@dataclass
class User:
    uid: str                    # the OIDC `sub`. Stable, never the email.
    email: str = ""
    name: str = ""
    picture: str = ""
    local: bool = False

    def json(self) -> dict:
        return {"uid": self.uid, "email": self.email, "name": self.name,
                "picture": self.picture, "local": self.local}


LOCAL_USER = User(uid=LOCAL_UID, email="", name="Local", local=True)


class Users:
    """Everyone who has signed in. Grows on first sight, never shrinks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.by_uid: dict[str, User] = {LOCAL_UID: LOCAL_USER}

    def upsert(self, u: User) -> User:
        with self._lock:
            self.by_uid[u.uid] = u
            return u


users = Users()


def enabled() -> bool:
    return bool(settings.google_oauth_client_id)


def verify(token: str) -> User:
    """Verify a Google ID token. Raises on anything that is not clean."""
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token as google_id_token

    try:
        claims = google_id_token.verify_oauth2_token(
            token, grequests.Request(), settings.google_oauth_client_id)
    except Exception as exc:                            # noqa: BLE001
        raise HTTPException(401, f"invalid Google token: {exc}") from exc

    # verify_oauth2_token already checks signature, audience and expiry. The
    # issuer is checked here because the library historically has not, and a token
    # from the wrong issuer with the right audience is exactly the case worth
    # refusing rather than trusting.
    if claims.get("iss") not in _ISSUERS:
        raise HTTPException(401, f"unexpected issuer: {claims.get('iss')}")
    if not claims.get("sub"):
        raise HTTPException(401, "token carries no subject")

    return users.upsert(User(
        uid=claims["sub"],
        email=claims.get("email", ""),
        name=claims.get("name") or claims.get("given_name", ""),
        picture=claims.get("picture", "")))


def current_user(authorization: str | None = Header(default=None)) -> User:
    """FastAPI dependency. The one place a request becomes a person.

    With auth off, returns the local user so every surface keeps working. With
    auth on, a missing or bad token is a 401 and no endpoint has to think about it.
    """
    if not enabled():
        return LOCAL_USER
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "sign in with Google to use this")
    return verify(authorization.split(" ", 1)[1].strip())
