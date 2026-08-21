"""Session tokens.

There is no app-native account. After a successful connect we hand the browser a
signed JWT whose subject is the user's SWID (ESPN's own member id). The token
carries no ESPN cookie material — it is only a claim of "this browser proved it
holds cookies for this SWID at time T".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

ALGORITHM = "HS256"
ISSUER = "espn-fantasy-dashboard"


class AuthError(Exception):
    """Raised for a missing, malformed, expired, or wrongly-signed token."""


def issue_token(swid: str, secret: str, *, ttl_hours: int) -> tuple[str, datetime]:
    if not secret:
        raise AuthError("JWT_SECRET is not set")
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=ttl_hours)
    payload = {
        "sub": swid,
        "iss": ISSUER,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM), expires_at


def read_token(token: str, secret: str) -> str:
    """Return the SWID a valid token vouches for."""
    if not secret:
        raise AuthError("JWT_SECRET is not set")
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM], issuer=ISSUER)
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("session expired; reconnect your ESPN cookies") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("invalid session token") from exc
    swid = payload.get("sub")
    if not swid:
        raise AuthError("session token has no subject")
    return swid


def bearer_from_header(header: str | None) -> str:
    if not header:
        raise AuthError("missing Authorization header")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("expected 'Authorization: Bearer <token>'")
    return token.strip()
