"""Health checks — never reveal keys or connection strings."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter

from app.database import healthcheck_database
from app.errors import ok

router = APIRouter(tags=["health"])

SERVICE_NAME = "quran-competition-server"


@router.get("/health")
async def health() -> dict[str, Any]:
    """Liveness check (no database access)."""
    return ok(
        {
            "status": "ok",
            "service": SERVICE_NAME,
            "commit": os.environ.get("RENDER_GIT_COMMIT", "local"),
        }
    )


@router.get("/health/database")
async def health_database() -> dict[str, Any]:
    """Readiness check: verifies the Supabase connection with one cheap query."""
    healthcheck_database()
    return ok({"status": "ok", "database": "connected", "service": SERVICE_NAME})