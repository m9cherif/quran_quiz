"""Authentication helpers.

- Admin: Bearer token compared with a constant-time comparison against the
  ADMIN_API_KEY from the environment. The architecture isolates verification
  here so it can later be swapped for Supabase Auth without touching routes.
- Participants: opaque access token issued at join time and stored in the
  participants table. The token (not the display name) is the identity.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import Header

from app.config import settings
from app.database import fetch_one
from app.errors import APIError

TOKEN_BYTES = 32


def _constant_time_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


async def require_admin(
    authorization: str | None = Header(default=None),
) -> str:
    """Dependency guarding every /api/admin route (Bearer admin key)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise APIError(
            "NOT_AUTHORIZED",
            "A valid admin bearer token is required.",
            status_code=401,
        )
    supplied = authorization[7:].strip()
    if not _constant_time_equal(supplied, settings.admin_api_key):
        raise APIError(
            "NOT_AUTHORIZED", "Invalid admin credentials.", status_code=401
        )
    return supplied


def generate_participant_token() -> str:
    """Cryptographically secure opaque token for a participant session."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def generate_short_participant_code() -> str:
    """Short, human-friendly code shown in the host room (e.g. 'K7S2P9')."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous characters
    return "".join(secrets.choice(alphabet) for _ in range(6))


def extract_bearer(request) -> str | None:
    """Read a Bearer token from the Authorization header, if present."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        value = authorization[7:].strip()
        return value or None
    return None


def resolve_participant_row(competition_id: str, token: str) -> dict[str, Any]:
    """Load the participant row identified by the token.

    Raises NOT_AUTHORIZED with a uniform message so an attacker cannot probe
    whether a given session exists.
    """
    if not token:
        raise APIError(
            "NOT_AUTHORIZED", "A participant token is required.", status_code=401
        )
    participant = fetch_one(
        "participants",
        conditions={"competition_id": competition_id, "access_token": token},
    )
    if participant is None:
        raise APIError(
            "NOT_AUTHORIZED",
            "Invalid or expired participant session.",
            status_code=401,
        )
    return participant