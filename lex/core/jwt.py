"""Local, network-free JWT expiry checks.

Adapted from oreilly-ingest ``core/http_client.py`` (``get_jwt_status`` /
``_decode_jwt_payload``), but credential-name-agnostic: Lexis's session token is
not assumed to be ``orm-jwt``. Pass any token string and we decode its ``exp``
so the tool can say "session is dead, re-authenticate" before spending a request.
"""

from __future__ import annotations

import base64
import json
import time


def looks_like_jwt(value: str) -> bool:
    """Cheap structural check: three non-empty dot-separated segments."""
    parts = value.split(".")
    return len(parts) == 3 and all(parts)


def decode_payload(token: str) -> dict | None:
    """Base64url-decode the JWT payload segment without verifying the signature."""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None


def token_status(token: str, skew_seconds: int = 60) -> dict:
    """Return ``{valid, reason, expires_at}`` for a token, treating near-expiry
    (within ``skew_seconds``) as invalid."""
    payload = decode_payload(token)
    if not payload:
        return {"valid": False, "reason": "invalid_token", "expires_at": None}
    exp = payload.get("exp", 0)
    expires_at = (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp)) if exp else None
    )
    if not exp or time.time() > exp - skew_seconds:
        return {"valid": False, "reason": "token_expired", "expires_at": expires_at}
    return {"valid": True, "reason": None, "expires_at": expires_at}


def first_valid_token_status(cookies: dict[str, str]) -> dict | None:
    """Scan a cookie dict for any JWT-shaped value and return the status of the
    one that expires latest (the live session token). None if no JWT is present —
    in that case fall back to login-redirect detection."""
    best = None
    for value in cookies.values():
        if not value or not looks_like_jwt(value):
            continue
        payload = decode_payload(value)
        if not payload or "exp" not in payload:
            continue
        status = token_status(value)
        if best is None or (payload["exp"] > best[0]):
            best = (payload["exp"], status)
    return best[1] if best else None
