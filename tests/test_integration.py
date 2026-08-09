"""End-to-end integration tests.

These exercise the full flow against the real Supabase project configured in
.env: competition -> questions -> join -> start -> answer (right, wrong, late,
duplicate) -> leaderboard -> WebSocket. They are skipped automatically when
the Supabase keys are not present in the environment.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import skip_no_database

pytestmark = pytest.mark.integration


def _admin(client: TestClient) -> dict[str, str]:
    from app.config import settings

    return {"Authorization": f"Bearer {settings.admin_api_key}"}


def _create_competition(client: TestClient, code: str) -> dict[str, Any]:
    response = client.post(
        "/api/admin/competitions",
        headers=_admin(client),
        json={"name": f"Integration {code}", "code": code},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _add_mcq_question(
    client: TestClient, competition_id: str, position: int
) -> dict[str, Any]:
    response = client.post(
        f"/api/admin/competitions/{competition_id}/questions",
        headers=_admin(client),
        json={
            "position": position,
            "text": f"Question {position}: quelle sourate contient 30 versets ?",
            "type": "mcq",
            "duration_seconds": 12,
            "points": 10,
            "negative_points": -2,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _add_choices(
    client: TestClient, question_id: str, correct_index: int
) -> list[dict[str, Any]]:
    choices = []
    labels = ["Al-Mulk", "An-Naba", "Yasin", "Maryam"]
    for index, label in enumerate(labels, start=1):
        response = client.post(
            f"/api/admin/questions/{question_id}/choices",
            headers=_admin(client),
            json={
                "text": label,
                "position": index,
                "is_correct": index - 1 == correct_index,
            },
        )
        assert response.status_code == 200, response.text
        choices.append(response.json()["data"])
    return choices


def _join(client: TestClient, code: str, name: str) -> dict[str, Any]:
    response = client.post(
        "/api/competitions/join",
        json={"competition_code": code, "display_name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.fixture(scope="module")
def flow(client: TestClient) -> dict[str, Any]:
    skip_no_database()
    competition = _create_competition(client, f"INTEG{int(time.time()) % 100000}")
    question = _add_mcq_question(client, competition["id"], 1)
    choices = _add_choices(client, question["id"], correct_index=0)
    player_a = _join(client, competition["code"], "Ahmed")
    player_b = _join(client, competition["code"], "Bilal")
    return {
        "competition": competition,
        "question": question,
        "choices": choices,
        "a": player_a,
        "b": player_b,
    }


def test_admin_requires_key(client: TestClient):
    response = client.get("/api/admin/competitions")
    assert response.status_code == 401


def test_competition_and_question_created(client: TestClient, flow: dict[str, Any]):
    comp = flow["competition"]
    response = client.get(
        f"/api/admin/competitions/{comp['id']}", headers=_admin(client)
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["code"] == comp["code"]
    assert body["questions_count"] == 1


def test_waitroom_info(client: TestClient, flow: dict[str, Any]):
    response = client.get(
        f"/api/competitions/{flow['competition']['id']}/waitroom",
        headers={"Authorization": f"Bearer {flow['a']['access_token']}"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["competition_name"] == flow["competition"]["name"]
    assert data["participant_name"] == "Ahmed"


def test_answer_before_start_rejected(client: TestClient, flow: dict[str, Any]):
    response = client.post(
        f"/api/competitions/{flow['competition']['id']}/answers",
        headers={"Authorization": f"Bearer {flow['a']['access_token']}"},
        json={"question_id": flow["question"]["id"], "choice_id": None},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] in (
        "QUESTION_NOT_ACTIVE",
        "COMPETITION_NOT_RUNNING",
        "INVALID_ANSWER",
    )


def test_join_after_start_rejected(client: TestClient, flow: dict[str, Any]):
    client.post(
        f"/api/admin/competitions/{flow['competition']['id']}/start",
        headers=_admin(client),
    )
    response = client.post(
        "/api/competitions/join",
        json={"competition_code": flow["competition"]["code"], "display_name": "Late"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "COMPETITION_NOT_ACCEPTING_PARTICIPANTS"


def test_question_start_correct_and_wrong_answers(
    client: TestClient, flow: dict[str, Any]
):
    competition_id = flow["competition"]["id"]
    response = client.post(
        f"/api/admin/competitions/{competition_id}/questions/{flow['question']['id']}/start",
        headers=_admin(client),
    )
    assert response.status_code == 200, response.text

    correct = client.post(
        f"/api/competitions/{competition_id}/answers",
        headers={"Authorization": f"Bearer {flow['a']['access_token']}"},
        json={"question_id": flow["question"]["id"], "choice_id": flow["choices"][0]["id"]},
    )
    assert correct.status_code == 200, correct.text
    assert correct.json()["data"]["is_correct"] is True
    assert correct.json()["data"]["points"] == 10.0

    wrong = client.post(
        f"/api/competitions/{competition_id}/answers",
        headers={"Authorization": f"Bearer {flow['b']['access_token']}"},
        json={"question_id": flow["question"]["id"], "choice_id": flow["choices"][1]["id"]},
    )
    assert wrong.status_code == 200, wrong.text
    assert wrong.json()["data"]["is_correct"] is False
    assert wrong.json()["data"]["points"] == -2.0


def test_duplicate_answer_rejected(client: TestClient, flow: dict[str, Any]):
    competition_id = flow["competition"]["id"]
    response = client.post(
        f"/api/competitions/{competition_id}/answers",
        headers={"Authorization": f"Bearer {flow['a']['access_token']}"},
        json={"question_id": flow["question"]["id"], "choice_id": flow["choices"][0]["id"]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ALREADY_ANSWERED"


def test_late_answer_rejected(client: TestClient, flow: dict[str, Any]):
    """Short-duration question: submit after the window closes."""
    competition_id = flow["competition"]["id"]
    response = client.post(
        f"/api/admin/competitions/{competition_id}/questions",
        headers=_admin(client),
        json={
            "position": 2,
            "text": "Réponse rapide abusive",
            "type": "text",
            "duration_seconds": 1,
            "correct_answer_text": "bonne",
        },
    )
    q2 = response.json()["data"]
    client.post(
        f"/api/admin/competitions/{competition_id}/questions/{q2['id']}/start",
        headers=_admin(client),
    )
    time.sleep(2.2)
    late = client.post(
        f"/api/competitions/{competition_id}/answers",
        headers={"Authorization": f"Bearer {flow['a']['access_token']}"},
        json={"question_id": q2["id"], "answer_text": "bonne"},
    )
    assert late.status_code == 400
    assert late.json()["error"]["code"] == "QUESTION_EXPIRED"


def test_pause_resume_then_finish(client: TestClient, flow: dict[str, Any]):
    competition_id = flow["competition"]["id"]
    pause = client.post(
        f"/api/admin/competitions/{competition_id}/pause", headers=_admin(client)
    )
    assert pause.status_code == 200

    resume = client.post(
        f"/api/admin/competitions/{competition_id}/resume", headers=_admin(client)
    )
    assert resume.status_code == 200
    assert resume.json()["data"]["status"] == "running"

    finish = client.post(
        f"/api/admin/competitions/{competition_id}/finish", headers=_admin(client)
    )
    assert finish.status_code == 200
    assert finish.json()["data"]["status"] == "finished"


def test_answer_after_finish_rejected(client: TestClient, flow: dict[str, Any]):
    competition_id = flow["competition"]["id"]
    response = client.post(
        f"/api/competitions/{competition_id}/answers",
        headers={"Authorization": f"Bearer {flow['b']['access_token']}"},
        json={"question_id": flow["question"]["id"], "choice_id": flow["choices"][0]["id"]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "COMPETITION_NOT_RUNNING"


def test_leaderboard_after_finish(client: TestClient, flow: dict[str, Any]):
    competition_id = flow["competition"]["id"]
    response = client.get(
        f"/api/competitions/{competition_id}/leaderboard",
        headers={"Authorization": f"Bearer {flow['a']['access_token']}"},
    )
    assert response.status_code == 200
    rows = response.json()["data"]
    assert rows[0]["display_name"] == "Ahmed"
    assert rows[0]["score"] >= 10.0
    assert rows[0]["answered_questions"] >= 1
    assert rows[0]["correct_answers"] >= 1


def test_websocket_flow(client: TestClient, flow: dict[str, Any]):
    """Connect as participant, identify, expect identify_required + state."""
    competition_id = flow["competition"]["id"]
    with client.websocket_connect(
        f"/ws/competition/{competition_id}"
    ) as websocket:
        first = websocket.receive_json()
        assert first["type"] == "identify_required"
        websocket.send_json(
            {
                "type": "identify",
                "role": "participant",
                "token": flow["b"]["access_token"],
            }
        )
        state = websocket.receive_json()
        assert state["type"] in ("competition_state", "participant_joined")
        assert state["competition_id"] == competition_id