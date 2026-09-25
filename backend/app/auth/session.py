"""Session handling: a signed JWT in an httpOnly cookie.

Hand-rolled rather than pulled from a framework because the requirement is
small and fully specified — sign in, sign out, know who is calling — and an
auth layer whose every line is auditable is worth more here than one whose
behaviour depends on library configuration.

The cookie is httpOnly (script cannot read it, blunting XSS token theft),
SameSite=Lax (blunts CSRF on state-changing POSTs while leaving top-level
navigation working), and Secure in production.

SameSite=Lax is only correct because the browser talks to a single origin: the
Next.js frontend proxies ``/api/*`` to this service, so the cookie is
first-party. A split-origin deployment would need SameSite=None; Secure, which
is materially weaker and is why the proxy was chosen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Response

from app.config import settings

SESSION_COOKIE = "groundwork_session"

_ISSUER = "groundwork"
_AUDIENCE = "groundwork-app"
_ALGORITHM = "HS256"
MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 days


def sign_session_token(user_id: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(seconds=MAX_AGE_SECONDS),
        "iss": _ISSUER,
        "aud": _AUDIENCE,
    }
    return jwt.encode(payload, settings.auth_secret, algorithm=_ALGORITHM)


def verify_session_token(token: str | None) -> str | None:
    """Return the user id, or None. Never raises — any failure means signed out."""
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.auth_secret,
            # Pinned: without an explicit algorithm list an attacker could
            # present an `alg: none` token, or force HMAC verification against
            # a public key.
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            audience=_AUDIENCE,
        )
    except jwt.PyJWTError:
        return None

    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None


def set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=sign_session_token(user_id),
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
        max_age=MAX_AGE_SECONDS,
    )


def clear_session_cookie(response: Response) -> None:
    # Attributes must match the ones used to set it, or some browsers keep the
    # original cookie and the user stays signed in.
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )
