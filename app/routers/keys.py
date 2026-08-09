"""Admin key self-service: anyone knowing the shared password can create
their own ADMIN API key through the web (/admin page).

The endpoint is public BY DESIGN but locked down:
- rate limited (5 attempts / minute / IP);
- password compared in constant time against env-configured values;
- only the SHA-256 hash of the new key is stored;
- the generated key is shown once and immediately usable.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.errors import APIError, ok
from app.models import APISuccess
from app.ratelimit import rate_limit
from app.security import key_store

logger = logging.getLogger("quran_quiz.keys")

router = APIRouter(prefix="/api/admin/key", tags=["admin-keys"])


class KeyGenerationRequest(BaseModel):
    """Body for POST /api/admin/key/generate."""

    password: str = Field(
        ..., min_length=1, max_length=100, description="Shared organizer password."
    )
    label: str | None = Field(
        None, max_length=100, description="Optional label to identify this key."
    )


class KeyGenerationResult(BaseModel):
    """A freshly minted admin API key (shown exactly once)."""

    access_token: str
    label: str | None = None


@router.post(
    "/generate",
    response_model=APISuccess[KeyGenerationResult],
    dependencies=[Depends(rate_limit(5, 60))],
)
async def generate_admin_key(payload: KeyGenerationRequest) -> dict[str, Any]:
    """Create a new admin API key (given the shared password).

    The returned token can be used immediately in /admin and on every
    /api/admin/* route. Keys are stored hashed; the env ADMIN_API_KEY
    remains valid too.
    """
    if not key_store.validate_password(payload.password):
        raise APIError(
            "NOT_AUTHORIZED", "Invalid password.", status_code=401
        )
    key = key_store.create_key(payload.label)
    logger.info(
        "ADMIN_KEY_CREATED label=%r", (payload.label or "").strip()
    )
    return ok({"access_token": key, "label": payload.label})