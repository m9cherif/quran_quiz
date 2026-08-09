"""Authentication helpers.

- Admin: Bearer token compared with a constant-time comparison against the
  ADMIN_API_KEY from the environment, plus any key created through the web
  (stored hashed in the `admin_keys` table). The architecture isolates
  verification here so it can later be swapped for Supabase Auth.
- Participants: opaque access token issued at join time and stored in the
  participants table. The token (not the display name) is the identity.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from typing import Any

from fastapi import Header

from app.config import settings
from app.database import fetch_many, fetch_one, insert_one
from app.errors import APIError

logger = logging.getLogger("quran_quiz.security")

TOKEN_BYTES = 32


def _constant_time_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class AdminKeyStore:
    """Valid admin keys = env ADMIN_API_KEY + hashes stored in admin_keys.

    The hash cache avoids a database read on every admin request; it is
    refreshed after generation and lazily refreshed when a token fails.
    """

    def __init__(self) -> None:
        self._env_key = settings.admin_api_key
        self._hashes: set[str] = set()
        self._refreshing = asyncio.Lock()

    async def refresh(self) -> None:
        async with self._refreshing:
            try:
                rows = fetch_many("admin_keys", columns="key_hash")
                self._hashes = {row["key_hash"] for row in rows}
            except APIError:
                logger.warning("Could not refresh admin key hashes from database.")

    async def is_valid(self, token: str) -> bool:
        if _constant_time_equal(token, self._env_key):
            return True
        digest = _hash_key(token)
        if digest in self._hashes:
            return True
        # Stale cache (e.g. key created on another instance): refresh once.
        await self.refresh()
        return digest in self._hashes

    def validate_password(self, password: str) -> bool:
        """Check the web-creation password against the configured list."""
        if not password:
            return False
        return any(
            _constant_time_equal(password, expected)
            for expected in settings.admin_key_passwords
        )

    def create_key(self, label: str | None = None) -> str:
        """Generate, persist (hashed) and register a new admin key."""
        key = secrets.token_urlsafe(48)
        digest = _hash_key(key)
        insert_one(
            "admin_keys",
            {"key_hash": digest, "label": label.strip() if label else None},
        )
        self._hashes.add(digest)
        return key


key_store = AdminKeyStore()


async def require_admin(
    authorization: str | None = Header(default=None),
) -> str:
    """Dependency guarding every /api/admin route."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise APIError(
            "NOT_AUTHORIZED",
            "A valid admin bearer token is required.",
            status_code=401,
        )
    supplied = authorization[7:].strip()
    if not await key_store.is_valid(supplied):
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