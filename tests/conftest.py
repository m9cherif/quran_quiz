"""Test fixtures.

Unit tests run without any real Supabase access.
Integration tests use the real Supabase project configured in .env and are
skipped automatically when the keys are missing or unreachable.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

DB_REQUIRED_VARS = ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "ADMIN_API_KEY")


@pytest.fixture(scope="session")
def client() -> TestClient:
    from main import app

    with TestClient(app) as test_client:
        yield test_client


def needs_database() -> bool:
    """True when real-looking Supabase credentials are present in the env."""
    url = os.getenv("SUPABASE_URL", "")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    admin_key = os.getenv("ADMIN_API_KEY", "")
    if "placeholder" in (url + service_key + admin_key):
        return False
    return bool(url and service_key and admin_key)


def skip_no_database() -> None:
    if not needs_database():
        pytest.skip(
            "Supabase keys not configured in the environment; "
            "skipping database integration test."
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_ts(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def ts_plus_seconds(seconds: int) -> str:
    return make_ts(utcnow() + timedelta(seconds=seconds))


@pytest.fixture(scope="session")
def admin_headers() -> dict[str, str]:
    from app.config import settings

    return {"Authorization": f"Bearer {settings.admin_api_key}"}


@pytest.fixture(scope="session")
def quran_competition(
    client: TestClient, admin_headers: dict[str, str]
) -> dict[str, Any]:
    """Create + configure a reusable competition fixture (DB integration)."""
    skip_no_database()
    response = client.post(
        "/api/admin/competitions",
        headers=admin_headers,
        json={"name": "Quran Test Comp", "code": "QTEST01"},
    )
    assert response.status_code == 200, response.text
    return {"id": response.json()["data"]["id"], "code": "QTEST01"}


@pytest.fixture(scope="session")
def seeded_question(
    client: TestClient,
    admin_headers: dict[str, str],
    quran_competition: dict[str, Any],
) -> dict[str, Any]:
    competition_id = quran_competition["id"]
    response = client.post(
        f"/api/admin/competitions/{competition_id}/questions",
        headers=admin_headers,
        json={
            "position": 1,
            "text": "Combien de versets contient la sourate Al-Fatiha ?",
            "type": "number",
            "duration_seconds": 15,
            "points": 10,
            "negative_points": -2,
            "correct_answer_text": "7",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]