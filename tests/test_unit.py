"""Pure-logic unit tests that need no database."""

from __future__ import annotations

import pytest

from app.errors import APIError, ok


def test_error_codes_are_enumerable():
    from app.errors import ERROR_CODES

    assert "QUESTION_EXPIRED" in ERROR_CODES
    assert "ALREADY_ANSWERED" in ERROR_CODES
    assert "NOT_AUTHORIZED" in ERROR_CODES


def test_api_error_unknown_code_rejected():
    with pytest.raises(ValueError):
        APIError("NOT_A_REAL_CODE", "nope")


def test_ok_envelope():
    assert ok({"a": 1}) == {"success": True, "data": {"a": 1}}
    assert ok() == {"success": True, "data": None}


def test_normalize_text_is_strict_but_fair():
    from app.game import normalize_text, parse_number

    assert normalize_text("  La   Louange   ") == "la louange"
    assert normalize_text("Louange") == "louange"
    assert parse_number(" 7 ") == 7.0
    assert parse_number("7,5") == 7.5


def test_scoring_regular_flow():
    from app.game import compute_points

    competition = {
        "default_points": 10,
        "default_negative_points": -2,
        "speed_bonus_enabled": False,
    }
    question = {"points": None, "negative_points": None, "duration_seconds": 15}
    points, bonus = compute_points(competition, question, True, 3000)
    assert points == 10.0 and bonus == 0.0
    points, bonus = compute_points(competition, question, False, 3000)
    assert points == -2.0 and bonus == 0.0


def test_scoring_per_question_overrides():
    from app.game import compute_points

    competition = {
        "default_points": 10,
        "default_negative_points": -2,
        "speed_bonus_enabled": False,
    }
    question = {"points": 25, "negative_points": -5}
    points, _ = compute_points(competition, question, True, 3000)
    assert points == 25.0
    points, _ = compute_points(competition, question, False, 3000)
    assert points == -5.0


def test_scoring_speed_bonus_only_when_enabled():
    from app.game import compute_points

    base = {
        "default_points": 10,
        "default_negative_points": -2,
        "speed_bonus_enabled": True,
    }
    question = {"points": None, "negative_points": None, "duration_seconds": 10}
    points, bonus = compute_points(base, question, True, 0)
    assert bonus > 0 and points == 10.0 + bonus
    points, bonus = compute_points(base, question, True, 10_000)
    assert points == 10.0 and bonus == 0.0


def test_ratelimiter_enforces_window():
    from app.errors import APIError
    from app.ratelimit import RateLimiter

    limiter = RateLimiter()
    with pytest.raises(APIError):
        for _ in range(3):
            limiter.check("scope", "someone", max_requests=2, window_seconds=60)
    # different identity is not affected
    limiter.check("scope", "someone_else", max_requests=2, window_seconds=60)


def test_admin_token_guard_missing_header(client):
    response = client.get("/api/admin/competitions")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_AUTHORIZED"


def test_admin_token_guard_wrong_token(client):
    response = client.get(
        "/api/admin/competitions", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHORIZED"


def test_admin_key_generation_wrong_password(client):
    """The public key-generation endpoint rejects bad passwords (no DB.)"""
    response = client.post(
        "/api/admin/key/generate", json={"password": "not-the-password"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHORIZED"


def test_admin_key_store_password_validation():
    """Configured shared passwords pass; anything else fails."""
    from app.security import key_store

    assert key_store.validate_password("mohamed") is True
    assert key_store.validate_password("mahmoud") is True
    assert key_store.validate_password("other") is False
    assert key_store.validate_password("") is False


def test_admin_key_generation_requires_password(client):
    response = client.post("/api/admin/key/generate", json={"label": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"
    assert body["data"]["service"] == "quran-competition-server"


def test_ui_pages_render(client):
    """The web app pages must render (no DB needed)."""
    checks = {
        "/": "Quran",
        "/admin": "btn-unlock",
        "/join": "join-code",
        "/room/00000000-0000-0000-0000-000000000000": "view-waiting",
        "/static/css/app.css": ".landing",
        "/static/js/room.js": "serverCountdown",
    }
    for path, marker in checks.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert marker in response.text, f"{path} missing {marker}"


def test_unknown_route_returns_error_envelope(client):
    response = client.get("/definitely/not/here")
    assert response.status_code == 404
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_validation_error_envelope(client):
    response = client.post(
        "/api/competitions/join", json={"competition_code": "AB"}
    )
    assert response.status_code == 422
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_join_requires_valid_competition(client):
    response = client.post(
        "/api/competitions/join",
        json={"competition_code": "NOPE123", "display_name": "Ahmed"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "COMPETITION_NOT_FOUND"


def test_join_validation_display_name(client):
    response = client.post(
        "/api/competitions/join", json={"competition_code": "QTEST01", "display_name": "x"}
    )
    assert response.status_code == 422


def test_leaderboard_requires_session(client):
    response = client.get("/api/competitions/00000000-0000-0000-0000-000000000000/leaderboard")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "NOT_AUTHORIZED"


def test_question_started_payload_has_no_correct_answer():
    """The question_started payload must never expose correct answers."""
    from datetime import datetime, timedelta, timezone

    from app.game import CompetitionState, question_started_payload

    now = datetime.now(timezone.utc)
    state = CompetitionState(
        competition_id="comp-1",
        status="running",
        question_id="q-1",
        question_position=3,
        started_at=now,
        duration_seconds=15,
        ends_at=now + timedelta(seconds=15),
    )
    question = {
        "id": "q-1",
        "position": 3,
        "text": "Quelle sourate contient 30 versets ?",
        "type": "text",
        "duration_seconds": 15,
        "audio_url": None,
        "correct_answer_text": "Al-Mulk",
    }
    payload = question_started_payload(
        state, question, now, now + timedelta(seconds=15)
    )
    payload_json = str(payload)
    assert "correct" not in payload_json.lower()
    assert "Al-Mulk" not in payload_json
    assert payload["question_id"] == "q-1"
    assert payload["type"] == "question_started"
    assert payload["question_type"] == "text"
    assert "choices" not in payload


def test_check_answer_window_classification():
    from datetime import datetime, timedelta, timezone

    from app.game import (
        ANSWER_STATUS_ACTIVE,
        ANSWER_STATUS_EXPIRED,
        ANSWER_STATUS_NOT_ACTIVE,
        ANSWER_STATUS_PAUSED,
        CompetitionState,
        GameService,
    )

    now = datetime.now(timezone.utc)
    state = CompetitionState(
        competition_id="c",
        status="running",
        question_id="q",
        started_at=now,
        duration_seconds=15,
        ends_at=now + timedelta(seconds=15),
    )
    service = GameService(None)
    assert service.check_answer_window(state, "q") == ANSWER_STATUS_ACTIVE
    assert service.check_answer_window(state, "other") == ANSWER_STATUS_NOT_ACTIVE
    state.paused = True
    assert service.check_answer_window(state, "q") == ANSWER_STATUS_PAUSED
    state.paused = False
    state.ends_at = now - timedelta(seconds=1)
    assert service.check_answer_window(state, "q") == ANSWER_STATUS_EXPIRED