"""Game engine: server-authoritative timing, pause handling, scoring.

The SERVER is the only time authority:
- Question windows are computed from server UTC timestamps.
- `started_at` / `ends_at` are persisted in Supabase and restored on restart.
- A 1-second clock loop broadcasts `question_ended`; answers are rejected once
  the window has passed, no matter what a client claims.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.database import (
    fetch_many,
    fetch_one,
    insert_one,
    update_one,
    utcnow,
)
from app.errors import APIError
from app.models import ALLOWED_QUESTION_TYPES
from app.ws_manager import (
    EVENT_COMPETITION_FINISHED,
    EVENT_COMPETITION_PAUSED,
    EVENT_COMPETITION_RESUMED,
    EVENT_COMPETITION_STARTED,
    EVENT_QUESTION_ENDED,
    EVENT_QUESTION_STARTED,
    Connection,
    ConnectionManager,
)

logger = logging.getLogger("quran_quiz.game")

CLOCK_TICK_SECONDS = 1.0

ANSWER_STATUS_ACTIVE = "ACTIVE"
ANSWER_STATUS_NOT_ACTIVE = "NOT_ACTIVE"
ANSWER_STATUS_EXPIRED = "EXPIRED"
ANSWER_STATUS_PAUSED = "PAUSED"


@dataclass
class CompetitionState:
    """In-memory timing state for one competition (rebuilt on request)."""

    competition_id: str
    status: str
    question_id: str | None = None
    question_position: int | None = None
    started_at: datetime | None = None
    duration_seconds: int | None = None
    ends_at: datetime | None = None
    paused: bool = False
    paused_seconds: float = 0.0
    paused_at: datetime | None = None
    question_ended_flag: bool = False
    answer_count: int = 0


def _now() -> datetime:
    return utcnow()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def safe_choices(question: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Choices WITHOUT is_correct — safe for participants."""
    return [
        {"id": row["id"], "text": row["text"], "position": row["position"]}
        for row in fetch_many(
            "choices",
            conditions={"question_id": question["id"]},
            order="position",
            ascending=True,
        )
    ]


def admin_choices(question: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Choices WITH is_correct — admin only."""
    return [
        {
            "id": row["id"],
            "question_id": row["question_id"],
            "text": row["text"],
            "position": row["position"],
            "is_correct": row["is_correct"],
        }
        for row in fetch_many(
            "choices",
            conditions={"question_id": question["id"]},
            order="position",
            ascending=True,
        )
    ]


def normalize_text(value: str) -> str:
    """Normalize a free-text answer for strict-but-fair comparison."""
    return " ".join(value.casefold().split())


def parse_number(value: str) -> float:
    cleaned = value.strip().replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        raise APIError(
            "INVALID_ANSWER", "The answer must be a number."
        ) from None


def check_answer_content(
    question: Mapping[str, Any],
    choice_id: str | None,
    answer_text: str | None,
) -> bool:
    """Server-side verification that the submitted value belongs to the question.

    Raises INVALID_ANSWER when the syntax does not fit the question type.
    For mcq/true_false the choice must exist for this question; comparison
    happens against the DB row, so a bogus choice_id fails even if it is a
    valid UUID of another question.
    """
    qtype = question.get("type")
    if qtype not in ALLOWED_QUESTION_TYPES:
        raise APIError("INVALID_ANSWER", "Unsupported question type.")
    if qtype in ("mcq", "true_false"):
        if not choice_id:
            raise APIError(
                "INVALID_ANSWER", "A choice is required for this question."
            )
        choice = fetch_one(
            "choices",
            conditions={"id": choice_id, "question_id": question["id"]},
        )
        if choice is None:
            raise APIError(
                "INVALID_ANSWER", "The selected choice does not exist."
            )
        return bool(choice["is_correct"])
    if qtype in ("text", "number"):
        if not answer_text or not answer_text.strip():
            raise APIError("INVALID_ANSWER", "An answer is required.")
        official = (question.get("correct_answer_text") or "").strip()
        if qtype == "number":
            if not question.get("correct_answer_text"):
                raise APIError("DATABASE_ERROR", "Question is missing its official answer.")
            haystack = parse_number(answer_text)
            needle = parse_number(official)
            return abs(haystack - needle) < 1e-9
        return normalize_text(answer_text) == normalize_text(official)
    return False


def compute_points(
    competition: Mapping[str, Any],
    question: Mapping[str, Any],
    is_correct: bool,
    response_time_ms: int,
) -> tuple[float, float]:
    """Return (awarded_points, bonus_points).

    Awards:
    - correct: question points (or competition default), plus an optional
      speed bonus when the competition enables it;
    - wrong: question negative_points (or competition default);
    - the "no answer" case (0) is simply never awarded — nothing is stored.
    """
    base = question.get("points")
    if base is None:
        base = competition.get("default_points", 10)
    penalty = question.get("negative_points")
    if penalty is None:
        penalty = competition.get("default_negative_points", -2)
    if not is_correct:
        return float(penalty), 0.0
    bonus = 0.0
    if competition.get("speed_bonus_enabled") and question.get("duration_seconds"):
        total_ms = float(question["duration_seconds"]) * 1000.0
        factor = max(0.0, 1.0 - response_time_ms / total_ms)
        bonus = round(float(base) * 0.5 * factor, 2)
    return round(float(base) + bonus, 2), round(bonus, 2)


def question_started_payload(
    state: CompetitionState,
    question: Mapping[str, Any],
    started_at: datetime,
    ends_at: datetime,
) -> dict[str, Any]:
    """Payload broadcast on question start — never contains correct answers."""
    payload: dict[str, Any] = {
        "type": EVENT_QUESTION_STARTED,
        "competition_id": state.competition_id,
        "question_id": question["id"],
        "position": question["position"],
        "text": question["text"],
        "type": question["type"],
        "duration_seconds": question["duration_seconds"],
        "started_at": _iso(started_at),
        "ends_at": _iso(ends_at),
    }
    if question.get("audio_url"):
        payload["audio_url"] = question["audio_url"]
    if question.get("type") in ("mcq", "true_false"):
        payload["choices"] = safe_choices(question)
    return payload


class GameService:
    """Owns the authoritative timing state and WS broadcasts per competition."""

    def __init__(self, manager: ConnectionManager) -> None:
        self.manager = manager
        self._states: dict[str, CompetitionState] = {}
        self._loop_running = False

    # -- state helpers ------------------------------------------------------

    def get_state(self, competition_id: str) -> CompetitionState | None:
        return self._states.get(competition_id)

    def drop(self, competition_id: str) -> None:
        """Forget all in-memory state for a deleted competition."""
        self._states.pop(competition_id, None)

    def state_for(self, competition: Mapping[str, Any]) -> CompetitionState:
        state = self._states.get(competition["id"])
        if state is None:
            state = CompetitionState(
                competition_id=competition["id"],
                status=str(competition.get("status") or "draft"),
                paused_seconds=float(competition.get("paused_seconds") or 0.0),
            )
            self._states[competition["id"]] = state
        else:
            state.status = str(competition.get("status") or state.status)
        return state

    # -- phase transitions (admin-triggered) --------------------------------

    async def start_competition(self, competition: Mapping[str, Any]) -> None:
        now = _now()
        update_one(
            "competitions",
            conditions={"id": competition["id"]},
            payload={
                "status": "running",
                "started_at": now.isoformat(),
                "paused_seconds": 0,
            },
        )
        state = self.state_for(competition)
        state.status = "running"
        state.paused = False
        state.paused_seconds = 0.0
        await self.manager.broadcast_all(
            competition["id"],
            {
                "type": EVENT_COMPETITION_STARTED,
                "competition_id": competition["id"],
                "started_at": _iso(now),
            },
        )
        logger.info("COMPETITION_STARTED id=%s", competition["id"])

    async def pause_competition(self, competition: Mapping[str, Any]) -> None:
        state = self.state_for(competition)
        if state.status != "running":
            raise APIError(
                "COMPETITION_NOT_RUNNING",
                "Only a running competition can be paused.",
            )
        now = _now()
        update_one(
            "competitions",
            conditions={"id": competition["id"]},
            payload={"status": "paused"},
        )
        state.status = "paused"
        state.paused = True
        state.paused_at = now
        await self.manager.broadcast_all(
            competition["id"],
            {
                "type": EVENT_COMPETITION_PAUSED,
                "competition_id": competition["id"],
                "paused_at": _iso(now),
            },
        )
        logger.info("COMPETITION_PAUSED id=%s", competition["id"])

    async def resume_competition(self, competition: Mapping[str, Any]) -> None:
        state = self.state_for(competition)
        if state.status != "paused":
            raise APIError(
                "COMPETITION_NOT_RUNNING",
                "Only a paused competition can be resumed.",
            )
        now = _now()
        if state.paused_at is not None:
            state.paused_seconds += (now - state.paused_at).total_seconds()
            state.paused_at = None
        update_one(
            "competitions",
            conditions={"id": competition["id"]},
            payload={
                "status": "running",
                "paused_seconds": round(state.paused_seconds, 3),
            },
        )
        state.status = "running"
        state.paused = False
        if state.question_id and state.started_at is not None:
            state.ends_at = self._effective_end(state, now)
            update_one(
                "questions",
                conditions={"id": state.question_id},
                payload={"ends_at": state.ends_at.isoformat()},
            )
        await self.manager.broadcast_all(
            competition["id"],
            {
                "type": EVENT_COMPETITION_RESUMED,
                "competition_id": competition["id"],
                "resumed_at": _iso(now),
            },
        )
        logger.info("COMPETITION_RESUMED id=%s", competition["id"])

    async def finish_competition(self, competition: Mapping[str, Any]) -> None:
        now = _now()
        update_one(
            "competitions",
            conditions={"id": competition["id"]},
            payload={
                "status": "finished",
                "finished_at": now.isoformat(),
            },
        )
        state = self.state_for(competition)
        state.status = "finished"
        state.question_id = None
        state.started_at = None
        state.ends_at = None
        state.paused = False
        await self.manager.broadcast_all(
            competition["id"],
            {
                "type": EVENT_COMPETITION_FINISHED,
                "competition_id": competition["id"],
                "finished_at": _iso(now),
            },
        )
        logger.info("COMPETITION_FINISHED id=%s", competition["id"])

    # -- question lifecycle -------------------------------------------------

    async def start_question(
        self,
        competition: Mapping[str, Any],
        question: Mapping[str, Any],
    ) -> CompetitionState:
        state = self.state_for(competition)
        if state.status != "running":
            raise APIError(
                "COMPETITION_NOT_RUNNING",
                "Start the competition before launching a question.",
            )
        if state.question_id is not None and not state.question_ended_flag:
            raise APIError(
                "QUESTION_NOT_ACTIVE",
                "Another question is currently active; wait for it to end.",
            )
        now = _now()
        duration = int(question["duration_seconds"])
        ends_at = now + timedelta(seconds=duration)
        state.question_id = question["id"]
        state.question_position = question["position"]
        state.started_at = now
        state.duration_seconds = duration
        state.ends_at = ends_at
        state.paused = False
        state.paused_seconds = 0.0
        state.question_ended_flag = False
        state.answer_count = 0
        try:
            update_one(
                "questions",
                conditions={"id": question["id"]},
                payload={
                    "started_at": now.isoformat(),
                    "ends_at": ends_at.isoformat(),
                },
            )
        except Exception:  # noqa: BLE001 — best effort persistence
            logger.warning(
                "Could not persist question window (qid=%s)", question["id"]
            )
        await self.manager.broadcast_all(
            competition["id"],
            question_started_payload(state, question, now, ends_at),
        )
        logger.info(
            "QUESTION_STARTED id=%s comp=%s duration=%ds",
            question["id"],
            competition["id"],
            duration,
        )
        return state

    # -- server-side evaluation --------------------------------------------

    def check_answer_window(
        self, state: CompetitionState, question_id: str
    ) -> str:
        """Classify a submit attempt for the active question window."""
        if state.question_id != question_id or state.question_ended_flag:
            return ANSWER_STATUS_NOT_ACTIVE
        if state.paused:
            return ANSWER_STATUS_PAUSED
        if state.ends_at is None or _now() > state.ends_at:
            return ANSWER_STATUS_EXPIRED
        return ANSWER_STATUS_ACTIVE

    async def mark_question_ended(self, state: CompetitionState) -> None:
        """Broadcast question_ended (idempotent)."""
        if state.question_ended_flag or state.question_id is None:
            return
        state.question_ended_flag = True
        await self.manager.broadcast_all(
            state.competition_id,
            {
                "type": EVENT_QUESTION_ENDED,
                "competition_id": state.competition_id,
                "question_id": state.question_id,
                "position": state.question_position,
                "ended_at": _iso(_now()),
            },
        )
        logger.info("QUESTION_ENDED id=%s", state.question_id)

    def _effective_end(
        self, state: CompetitionState, now: datetime
    ) -> datetime:
        """Window end = start + duration + accumulated pause time.

        A pause never silently consumes answer time: the end is shifted by the
        total duration of pauses that happened during the window.
        """
        assert state.started_at is not None
        return (
            state.started_at
            + timedelta(seconds=float(state.duration_seconds or 0))
            + timedelta(seconds=state.paused_seconds)
        )

    # -- clock loop ---------------------------------------------------------

    async def clock_loop(self) -> None:
        """Every second, expire finished questions and broadcast the event."""
        self._loop_running = True
        logger.info("GAME_CLOCK_STARTED")
        while True:
            await asyncio.sleep(CLOCK_TICK_SECONDS)
            now = _now()
            for state in list(self._states.values()):
                if state.question_id is None or state.question_ended_flag:
                    continue
                if state.paused:
                    continue
                if now > state.ends_at:
                    await self.mark_question_ended(state)

    # -- restore after restart (best effort) --------------------------------

    def restore_active_questions(self) -> None:
        """Re-attach to questions still in their window after a restart."""
        try:
            active = fetch_many(
                "competitions", conditions={"status": "running"}
            )
        except APIError:
            logger.warning("Could not restore game state from database.")
            return
        for competition in active:
            state = self.state_for(competition)
            candidates = fetch_many(
                "questions",
                conditions={"competition_id": competition["id"]},
                order="started_at",
                ascending=False,
                limit=1,
            )
            question = candidates[0] if candidates else None
            if question is None or not question.get("started_at") or not question.get("ends_at"):
                continue
            started_dt = datetime.fromisoformat(
                question["started_at"].replace("Z", "+00:00")
            )
            ends_dt = datetime.fromisoformat(
                question["ends_at"].replace("Z", "+00:00")
            )
            if _now() <= ends_dt:
                state.question_id = question["id"]
                state.question_position = question.get("position")
                state.started_at = started_dt
                state.duration_seconds = int(question.get("duration_seconds") or 0)
                state.ends_at = ends_dt
                state.paused = False
                state.paused_seconds = float(competition.get("paused_seconds") or 0.0)
                logger.info("RESTORED_ACTIVE_QUESTION id=%s", question["id"])