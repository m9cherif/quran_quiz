"""Participant-facing API: join, waiting room, answers, leaderboard.

Every endpoint validates the caller server-side; the only identity that
matters is the opaque access token issued at join time.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Request

from app import context
from app.config import settings
from app.database import (
    fetch_many,
    fetch_one,
    insert_one,
    is_unique_violation,
    parse_ts,
    unique_value,
    update_one,
    utcnow,
)
from app.errors import APIError, ok
from app.game import (
    ANSWER_STATUS_ACTIVE,
    ANSWER_STATUS_EXPIRED,
    ANSWER_STATUS_NOT_ACTIVE,
    ANSWER_STATUS_PAUSED,
    check_answer_content,
    compute_points,
)
from app.models import (
    APISuccess,
    AnswerReceipt,
    AnswerSubmission,
    JoinRequest,
    JoinResponse,
    LeaderboardEntry,
    WaitroomInfo,
)
from app.ratelimit import rate_limit
from app.security import (
    extract_bearer,
    generate_participant_token,
    generate_short_participant_code,
    resolve_participant_row,
)
from app.ws_manager import (
    EVENT_ANSWER_RECEIVED,
    EVENT_LEADERBOARD_UPDATED,
)

logger = logging.getLogger("quran_quiz.participants")

router = APIRouter(prefix="/api/competitions", tags=["participants"])

ACCEPTING_STATUSES = {"draft", "scheduled", "waiting"}


def _bearer(request: Request) -> str | None:
    return extract_bearer(request)


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------


@router.post(
    "/join",
    response_model=APISuccess[JoinResponse],
    dependencies=[Depends(rate_limit(10, 60))],
)
async def join_competition(payload: JoinRequest) -> dict[str, Any]:
    """Join a competition with its shareable code.

    Returns an opaque access token used for every subsequent request and for
    WebSocket identification.
    """
    competition = fetch_one(
        "competitions", {"code": payload.competition_code}
    )
    if competition is None:
        raise APIError(
            "COMPETITION_NOT_FOUND",
            "No competition found with this code.",
            404,
        )
    if competition["status"] not in ACCEPTING_STATUSES:
        hint = {
            "running": "The competition has already started — joins are closed.",
            "paused": "The competition is paused — joins are closed.",
        }.get(
            competition["status"],
            "This competition is not accepting new participants right now.",
        )
        raise APIError(
            "COMPETITION_NOT_ACCEPTING_PARTICIPANTS",
            hint,
            403,
        )
    now = utcnow().isoformat()
    existing = fetch_one(
        "participants",
        {"competition_id": competition["id"]},
        columns="*",
        ilike={"display_name": payload.display_name},
    )
    if existing is not None:
        # Same player coming back (another device/browser): keep their
        # identity instead of hitting the (competition_id, lower(display_name))
        # unique constraint. Their token stays valid.
        logger.info(
            "PARTICIPANT_REJOIN comp=%s participant=%s",
            competition["id"], existing["id"],
        )
        update_one(
            "participants",
            {"id": existing["id"]},
            {"connected": False, "last_seen_at": now},
        )
        return ok(
            {
                "competition_id": competition["id"],
                "competition_name": competition["name"],
                "competition_status": competition["status"],
                "participant_id": existing["id"],
                "participant_code": existing["participant_code"],
                "display_name": existing["display_name"],
                "access_token": existing["access_token"],
                "connected_participants": context.manager.count_identified(
                    competition["id"]
                ),
            }
        )
    access_token = generate_participant_token()
    participant_code = unique_value(
        "participants",
        column="participant_code",
        generate=generate_short_participant_code,
    )
    body = {
        "competition_id": competition["id"],
        "display_name": payload.display_name,
        "first_name": payload.first_name,
        "last_name": payload.last_name,
        "participant_code": participant_code,
        "access_token": access_token,
        "connected": False,
        "joined_at": now,
        "last_seen_at": now,
        "status": "joined",
    }
    participant = insert_one("participants", body)
    logger.info(
        "PARTICIPANT_JOINED comp=%s participant=%s",
        competition["id"],
        participant["id"],
    )
    return ok(
        {
            "competition_id": competition["id"],
            "competition_name": competition["name"],
            "competition_status": competition["status"],
            "participant_id": participant["id"],
            "participant_code": participant["participant_code"],
            "display_name": participant["display_name"],
            "access_token": participant["access_token"],
            "connected_participants": context.manager.count_identified(
                competition["id"]
            ),
        }
    )


# ---------------------------------------------------------------------------
# Waiting room
# ---------------------------------------------------------------------------


@router.get("/{competition_id}/waitroom", response_model=APISuccess[WaitroomInfo])
async def get_waitroom(competition_id: str, request: Request) -> dict[str, Any]:
    """Waiting-room info for an identified participant."""
    token = _bearer(request)
    participant = resolve_participant_row(competition_id, token or "")
    competition = fetch_one("competitions", {"id": competition_id})
    if competition is None:
        raise APIError("COMPETITION_NOT_FOUND", "Competition not found.", 404)
    return ok(
        {
            "competition_id": competition_id,
            "competition_name": competition["name"],
            "participant_id": participant["id"],
            "participant_name": participant["display_name"],
            "participant_code": participant.get("participant_code"),
            "competition_status": competition["status"],
            "connected_participants": context.manager.count_identified(
                competition_id
            ),
        }
    )


# ---------------------------------------------------------------------------
# Answers — the server is the only time authority.
# ---------------------------------------------------------------------------


def _answers_row(
    competition: dict[str, Any],
    participant: dict[str, Any],
    question: dict[str, Any],
    is_correct: bool,
    response_time_ms: int,
) -> dict[str, Any]:
    base, bonus = compute_points(
        competition, question, is_correct, response_time_ms
    )
    return {
        "competition_id": competition["id"],
        "question_id": question["id"],
        "participant_id": participant["id"],
        "is_correct": is_correct,
        "points": base,
        "bonus_points": bonus,
        "response_time_ms": response_time_ms,
        "submitted_at": utcnow().isoformat(),
    }


@router.post(
    "/{competition_id}/answers",
    response_model=APISuccess[AnswerReceipt],
    dependencies=[Depends(rate_limit(30, 60))],
)
async def submit_answer(
    competition_id: str, payload: AnswerSubmission, request: Request
) -> dict[str, Any]:
    """Submit one answer to the active question.

    Checks, in order: session validity, competition running, question active,
    server time (rejects late answers), duplicates, and answer validity.
    """
    token = _bearer(request)
    participant = resolve_participant_row(competition_id, token or "")
    competition = fetch_one("competitions", {"id": competition_id})
    if competition is None:
        raise APIError("COMPETITION_NOT_FOUND", "Competition not found.", 404)
    if competition["status"] != "running":
        if competition["status"] == "finished":
            raise APIError(
                "COMPETITION_NOT_RUNNING",
                "The competition is finished and answers are closed.",
            )
        raise APIError(
            "COMPETITION_NOT_RUNNING",
            "The competition is not accepting answers right now.",
        )

    state = context.game.state_for(competition)
    window = context.game.check_answer_window(state, payload.question_id)
    if window == ANSWER_STATUS_NOT_ACTIVE:
        raise APIError(
            "QUESTION_NOT_ACTIVE", "This question is not currently active."
        )
    if window == ANSWER_STATUS_PAUSED:
        raise APIError(
            "QUESTION_NOT_ACTIVE",
            "The competition is paused; the question timer is suspended.",
        )
    if window == ANSWER_STATUS_EXPIRED:
        raise APIError(
            "QUESTION_EXPIRED", "The answer time for this question has ended."
        )

    question = fetch_one(
        "questions",
        {
            "id": payload.question_id,
            "competition_id": competition_id,
        },
    )
    if question is None:
        raise APIError("QUESTION_NOT_ACTIVE", "Question not found.")

    existing = fetch_one(
        "answers",
        {
            "participant_id": participant["id"],
            "question_id": payload.question_id,
        },
        columns="id",
    )
    if existing is not None:
        raise APIError(
            "ALREADY_ANSWERED", "You already answered this question."
        )

    is_correct = check_answer_content(
        question, payload.choice_id, payload.answer_text
    )
    assert state.started_at is not None
    response_time_ms = int(
        (utcnow() - state.started_at).total_seconds() * 1000.0
    )
    row = _answers_row(
        competition, participant, question, is_correct, response_time_ms
    )
    if payload.choice_id:
        row["choice_id"] = payload.choice_id
    if payload.answer_text:
        row["answer_text"] = payload.answer_text.strip()[:1000]
    try:
        answer = insert_one("answers", row)
    except APIError as exc:
        if is_unique_violation(exc.__cause__ or exc):
            logger.warning(
                "Duplicate answer rejected (participant=%s question=%s)",
                participant["id"],
                payload.question_id,
            )
            raise APIError(
                "ALREADY_ANSWERED", "You already answered this question."
            ) from exc
        raise

    context.leaderboard.apply_answer(
        competition_id,
        participant["id"],
        participant["display_name"],
        payload.question_id,
        is_correct,
        float(row["points"]),
    )
    state.answer_count += 1
    logger.info(
        "ANSWER_%s participant=%s question=%s correct=%s time=%dms",
        "ACCEPTED" if is_correct is not None else "REJECTED",
        participant["id"],
        payload.question_id,
        is_correct,
        response_time_ms,
    )
    await context.manager.broadcast_all(
        competition_id,
        {
            "type": EVENT_ANSWER_RECEIVED,
            "competition_id": competition_id,
            "question_id": payload.question_id,
            "answered_count": state.answer_count,
        },
    )
    await context.manager.broadcast_all(
        competition_id,
        {"type": EVENT_LEADERBOARD_UPDATED, "competition_id": competition_id},
    )
    answered = context.leaderboard.answered_questions(competition_id)
    explanation = (
        question.get("explanation")
        if state.question_ended_flag or state.ends_at is None or utcnow() > state.ends_at
        else None
    )
    return ok(
        {
            "accepted": True,
            "is_correct": is_correct,
            "points": row["points"],
            "response_time_ms": response_time_ms,
            "explanation": explanation,
            "answered_questions": len(answered),
        }
    )


# ---------------------------------------------------------------------------
# Leaderboard (server-computed).
# ---------------------------------------------------------------------------


@router.get(
    "/{competition_id}/leaderboard",
    response_model=APISuccess[list[LeaderboardEntry]],
)
async def get_leaderboard(
    competition_id: str, request: Request
) -> dict[str, Any]:
    """Server-computed ranking: score is never accepted from a client.

    Requires a participant session token (admin may use the admin key).
    """
    token = _bearer(request)
    if not token:
        raise APIError(
            "NOT_AUTHORIZED", "A participant session is required.", 401
        )
    try:
        resolve_participant_row(competition_id, token)
    except APIError:
        if not secrets.compare_digest(
            token.encode(), settings.admin_api_key.encode()
        ):
            raise
    competition = fetch_one("competitions", {"id": competition_id})
    if competition is None:
        raise APIError("COMPETITION_NOT_FOUND", "Competition not found.", 404)
    rows = context.leaderboard.serve(competition_id)
    return ok(rows)