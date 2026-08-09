"""Admin API â€” protected by the ADMIN_API_KEY bearer token.

Note: questions created here may carry the official answer and choices with
is_correct; those fields never leave the admin API.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Request

from app import context
from app.database import (
    delete_many,
    fetch_many,
    fetch_one,
    insert_one,
    is_unique_violation,
    update_one,
    utcnow,
)
from app.errors import APIError, ok
from app.models import (
    APISuccess,
    ChoiceCreate,
    ChoiceUpdate,
    CompetitionCreate,
    CompetitionOut,
    QuestionAdminOut,
    QuestionCreate,
    QuestionUpdate,
)
from app.ratelimit import rate_limit
from app.security import require_admin

logger = logging.getLogger("quran_quiz.admin")

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin), Depends(rate_limit(120, 60))],
)

_COMPETITION_FIELDS = (
    "id, code, name, description, status, scheduled_at, started_at, finished_at,"
    " created_at, updated_at, default_points, default_negative_points,"
    " speed_bonus_enabled"
)


def _competition_out(row: dict[str, Any]) -> dict[str, Any]:
    def _char(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "description": _char(row.get("description")),
        "status": row.get("status"),
        "scheduled_at": _char(row.get("scheduled_at")),
        "started_at": _char(row.get("started_at")),
        "finished_at": _char(row.get("finished_at")),
        "created_at": _char(row.get("created_at")),
        "updated_at": _char(row.get("updated_at")),
        "default_points": row.get("default_points", 10),
        "default_negative_points": row.get("default_negative_points", -2),
        "speed_bonus_enabled": bool(row.get("speed_bonus_enabled", False)),
    }


def _load_competition(competition_id: str) -> dict[str, Any]:
    competition = fetch_one("competitions", {"id": competition_id})
    if competition is None:
        raise APIError("COMPETITION_NOT_FOUND", "Competition not found.", 404)
    return competition


def _load_question(question_id: str) -> dict[str, Any]:
    question = fetch_one("questions", {"id": question_id})
    if question is None:
        raise APIError("INVALID_REQUEST", "Question not found.", 404)
    return question


def _question_admin_out(question: dict[str, Any]) -> dict[str, Any]:
    from app.game import admin_choices

    return {
        "id": question["id"],
        "competition_id": question["competition_id"],
        "position": question["position"],
        "text": question["text"],
        "type": question["type"],
        "duration_seconds": question["duration_seconds"],
        "points": question.get("points"),
        "negative_points": question.get("negative_points"),
        "explanation": question.get("explanation"),
        "correct_answer_text": question.get("correct_answer_text"),
        "audio_url": question.get("audio_url"),
        "surah_number": question.get("surah_number"),
        "ayah_number": question.get("ayah_number"),
        "page_number": question.get("page_number"),
        "juz_number": question.get("juz_number"),
        "hizb_number": question.get("hizb_number"),
        "choices": admin_choices(question),
    }


def _generate_competition_code() -> str:
    days = [
        "AYAT", "NOOR", "QURAN", "FURQAN", "TAJWEED",
        "TARTEEL", "SAJDAH", "IMAN", "AMANAH", "SABR",
        "NISAA", "MARYAM", "TAHA", "YASIN", "RAHMAN",
        "WAQIAH", "MULK", "NABA", "BAQARAH", "SAFFAT",
    ]
    stem = secrets.choice(days)
    suffix = "".join(
        secrets.choice("0123456789") for _ in range(3)
    )
    return f"{stem}{suffix}"


# ---------------------------------------------------------------------------
# Competitions
# ---------------------------------------------------------------------------


@router.post("/competitions", response_model=APISuccess[CompetitionOut])
async def create_competition(payload: CompetitionCreate) -> dict[str, Any]:
    """Create a competition (draft by default)."""
    code = payload.code or _generate_competition_code()
    now = utcnow().isoformat()
    body: dict[str, Any] = {
        "code": code,
        "name": payload.name,
        "description": payload.description,
        "status": payload.status,
        "scheduled_at": payload.scheduled_at.isoformat() if payload.scheduled_at else None,
        "default_points": payload.default_points,
        "default_negative_points": payload.default_negative_points,
        "speed_bonus_enabled": payload.speed_bonus_enabled,
        "created_at": now,
        "updated_at": now,
    }
    try:
        row = insert_one("competitions", body)
    except APIError as exc:
        if is_unique_violation(exc.__cause__ or exc):
            raise APIError(
                "INVALID_REQUEST",
                "This competition code is already in use.",
            ) from exc
        raise
    return ok(_competition_out(row))


@router.get("/competitions", response_model=APISuccess[list[CompetitionOut]])
async def list_competitions() -> dict[str, Any]:
    """List all competitions, most recent first."""
    rows = fetch_many("competitions", order="created_at", ascending=False)
    return ok([_competition_out(r) for r in rows])


@router.get("/competitions/{competition_id}", response_model=APISuccess[dict[str, Any]])
async def get_competition(competition_id: str) -> dict[str, Any]:
    """Get one competition with its question/participant counts."""
    competition = _load_competition(competition_id)
    counts_questions = fetch_many(
        "questions",
        conditions={"competition_id": competition_id},
        columns="id",
    )
    counts_participants = fetch_many(
        "participants",
        conditions={"competition_id": competition_id},
        columns="id",
    )
    out = _competition_out(competition)
    out["questions_count"] = len(counts_questions)
    out["participants_count"] = len(counts_participants)
    return ok(out)


@router.get("/competitions/{competition_id}/participants", response_model=APISuccess[dict[str, Any]])
async def list_participants(competition_id: str) -> dict[str, Any]:
    """List participants of a competition (names public, tokens never shown)."""
    _load_competition(competition_id)
    rows = fetch_many(
        "participants",
        conditions={"competition_id": competition_id},
        order="joined_at",
        ascending=True,
    )
    safe = [
        {
            "id": r["id"],
            "display_name": r["display_name"],
            "participant_code": r.get("participant_code"),
            "connected": bool(r.get("connected", False)),
            "joined_at": r.get("joined_at"),
            "last_seen_at": r.get("last_seen_at"),
            "status": r.get("status"),
        }
        for r in rows
    ]
    return ok(safe)


@router.post("/competitions/{competition_id}/start", response_model=APISuccess[CompetitionOut])
async def start_competition(competition_id: str) -> dict[str, Any]:
    """Transition the competition to 'running' (waiting -> running)."""
    competition = _load_competition(competition_id)
    if competition["status"] not in ("draft", "scheduled", "waiting"):
        raise APIError(
            "COMPETITION_NOT_RUNNING",
            f"A competition in state '{competition['status']}' cannot be started.",
        )
    await context.game.start_competition(competition)
    competition["status"] = "running"
    competition["started_at"] = utcnow().isoformat()
    return ok(_competition_out(competition))


@router.post("/competitions/{competition_id}/pause", response_model=APISuccess[CompetitionOut])
async def pause_competition(competition_id: str) -> dict[str, Any]:
    """Pause the competition; an active question window is suspended."""
    competition = _load_competition(competition_id)
    await context.game.pause_competition(competition)
    competition["status"] = "paused"
    return ok(_competition_out(competition))


@router.post("/competitions/{competition_id}/resume", response_model=APISuccess[CompetitionOut])
async def resume_competition(competition_id: str) -> dict[str, Any]:
    """Resume a paused competition; the question clock keeps its remaining time."""
    competition = _load_competition(competition_id)
    await context.game.resume_competition(competition)
    competition["status"] = "running"
    return ok(_competition_out(competition))


@router.post("/competitions/{competition_id}/finish", response_model=APISuccess[CompetitionOut])
async def finish_competition(competition_id: str) -> dict[str, Any]:
    """End the competition: no more answers are accepted."""
    competition = _load_competition(competition_id)
    await context.game.finish_competition(competition)
    competition["status"] = "finished"
    competition["finished_at"] = utcnow().isoformat()
    context.leaderboard.invalidate(competition_id)
    return ok(_competition_out(competition))


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


@router.post("/competitions/{competition_id}/questions", response_model=APISuccess[QuestionAdminOut])
async def create_question(
    competition_id: str, payload: QuestionCreate
) -> dict[str, Any]:
    """Create a question inside a competition."""
    _load_competition(competition_id)
    body: dict[str, Any] = {
        "competition_id": competition_id,
        "position": payload.position,
        "text": payload.text,
        "type": payload.type,
        "duration_seconds": payload.duration_seconds,
        "points": payload.points,
        "negative_points": payload.negative_points,
        "explanation": payload.explanation,
        "correct_answer_text": payload.correct_answer_text,
        "audio_url": payload.audio_url,
        "surah_number": payload.surah_number,
        "ayah_number": payload.ayah_number,
        "page_number": payload.page_number,
        "juz_number": payload.juz_number,
        "hizb_number": payload.hizb_number,
    }
    try:
        row = insert_one("questions", body)
    except APIError as exc:
        if is_unique_violation(exc.__cause__ or exc):
            raise APIError(
                "INVALID_REQUEST",
                "A question already exists at this position.",
            ) from exc
        raise
    return ok(_question_admin_out(row))


@router.get(
    "/competitions/{competition_id}/questions",
    response_model=APISuccess[list[QuestionAdminOut]],
)
async def list_questions(competition_id: str) -> dict[str, Any]:
    """List all questions of a competition (with correct answers, admin only)."""
    _load_competition(competition_id)
    rows = fetch_many(
        "questions",
        conditions={"competition_id": competition_id},
        order="position",
        ascending=True,
    )
    return ok([_question_admin_out(r) for r in rows])


@router.put("/questions/{question_id}", response_model=APISuccess[QuestionAdminOut])
async def update_question(
    question_id: str, payload: QuestionUpdate
) -> dict[str, Any]:
    """Update editable fields of a question."""
    _load_question(question_id)
    body: dict[str, Any] = {
        key: value
        for key, value in payload.model_dump().items()
        if value is not None
    }
    if not body:
        raise APIError("INVALID_REQUEST", "Nothing to update.")
    row = update_one("questions", {"id": question_id}, body)
    return ok(_question_admin_out(row))


@router.delete("/questions/{question_id}", response_model=APISuccess[dict[str, Any]])
async def delete_question(question_id: str) -> dict[str, Any]:
    """Delete a question and its choices (cascade)."""
    question = _load_question(question_id)
    delete_many("questions", {"id": question_id})
    return ok({"deleted": True, "question_id": question_id})


@router.post(
    "/competitions/{competition_id}/questions/{question_id}/start",
    response_model=APISuccess[dict[str, Any]],
)
async def start_question(
    competition_id: str, question_id: str
) -> dict[str, Any]:
    """Launch a question: server timing begins, participants are notified.

    The correct answer is never included in the broadcast.
    """
    competition = _load_competition(competition_id)
    question = fetch_one(
        "questions", {"id": question_id, "competition_id": competition_id}
    )
    if question is None:
        raise APIError("INVALID_REQUEST", "Question not found in this competition.", 404)
    state = await context.game.start_question(competition, question)
    return ok(
        {
            "question_id": question_id,
            "started_at": state.started_at.isoformat() if state.started_at else None,
            "ends_at": state.ends_at.isoformat() if state.ends_at else None,
        }
    )


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------


@router.post("/questions/{question_id}/choices", response_model=APISuccess[dict[str, Any]])
async def create_choice(
    question_id: str, payload: ChoiceCreate
) -> dict[str, Any]:
    """Add a choice to a question (is_correct stays server-side)."""
    question = _load_question(question_id)
    if question["type"] not in ("mcq", "true_false"):
        raise APIError(
            "INVALID_REQUEST",
            "Only mcq/true_false questions have choices.",
        )
    body = {
        "question_id": question_id,
        "text": payload.text,
        "position": payload.position,
        "is_correct": payload.is_correct,
    }
    try:
        row = insert_one("choices", body)
    except APIError as exc:
        if is_unique_violation(exc.__cause__ or exc):
            raise APIError(
                "INVALID_REQUEST", "A choice already exists at this position."
            ) from exc
        raise
    return ok(
        {
            "id": row["id"],
            "question_id": row["question_id"],
            "text": row["text"],
            "position": row["position"],
            "is_correct": row["is_correct"],
        }
    )


@router.put("/choices/{choice_id}", response_model=APISuccess[dict[str, Any]])
async def update_choice(
    choice_id: str, payload: ChoiceUpdate
) -> dict[str, Any]:
    """Update a choice."""
    choice = fetch_one("choices", {"id": choice_id})
    if choice is None:
        raise APIError("INVALID_REQUEST", "Choice not found.", 404)
    body = {
        key: value
        for key, value in payload.model_dump().items()
        if value is not None
    }
    if not body:
        raise APIError("INVALID_REQUEST", "Nothing to update.")
    row = update_one("choices", {"id": choice_id}, body)
    return ok(
        {
            "id": row["id"],
            "question_id": row["question_id"],
            "text": row["text"],
            "position": row["position"],
            "is_correct": row["is_correct"],
        }
    )


@router.delete("/choices/{choice_id}", response_model=APISuccess[dict[str, Any]])
async def delete_choice(choice_id: str) -> dict[str, Any]:
    """Delete a choice."""
    if fetch_one("choices", {"id": choice_id}) is None:
        raise APIError("INVALID_REQUEST", "Choice not found.", 404)
    delete_many("choices", {"id": choice_id})
    return ok({"deleted": True, "choice_id": choice_id})
