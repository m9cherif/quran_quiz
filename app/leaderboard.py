"""Server-computed leaderboard with an in-memory cache.

The client can never influence the score: only answers accepted by
GameService feed this cache, and the full ranking is recomputed only when a
competition page is first requested after a restart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.database import fetch_many, fetch_one

logger = logging.getLogger("quran_quiz.leaderboard")


@dataclass
class _Entry:
    participant_id: str
    display_name: str
    score: float = 0.0
    correct_answers: int = 0
    answered_questions: set[str] = field(default_factory=set)


class LeaderboardService:
    def __init__(self) -> None:
        self._boards: dict[str, dict[str, _Entry]] = {}

    # -- maintenance --------------------------------------------------------

    def invalidate(self, competition_id: str) -> None:
        self._boards.pop(competition_id, None)

    def rebuild(self, competition_id: str) -> None:
        """Recompute a board straight from the answers table."""
        answers = fetch_many(
            "answers",
            conditions={"competition_id": competition_id},
            columns="participant_id, question_id, is_correct, points",
        )
        participants = {
            row["id"]: row
            for row in fetch_many(
                "participants",
                conditions={"competition_id": competition_id},
                columns="id, display_name",
            )
        }
        entries: dict[str, _Entry] = {}
        for answer in answers:
            pid = answer["participant_id"]
            entry = entries.get(pid)
            if entry is None:
                name = (participants.get(pid) or {}).get("display_name", "?")
                entry = _Entry(participant_id=pid, display_name=name)
                entries[pid] = entry
            entry.score += float(answer.get("points") or 0.0)
            entry.answered_questions.add(answer["question_id"])
            if answer.get("is_correct"):
                entry.correct_answers += 1
        self._boards[competition_id] = entries
        logger.info("LEADERBOARD_REBUILT comp=%s entries=%d", competition_id, len(entries))

    def _board(self, competition_id: str) -> dict[str, _Entry]:
        board = self._boards.get(competition_id)
        if board is None:
            self.rebuild(competition_id)
            board = self._boards[competition_id]
        return board

    # -- incremental updates ------------------------------------------------

    def apply_answer(
        self,
        competition_id: str,
        participant_id: str,
        display_name: str,
        question_id: str,
        is_correct: bool,
        points: float,
    ) -> None:
        board = self._board(competition_id)
        entry = board.setdefault(
            participant_id,
            _Entry(participant_id=participant_id, display_name=display_name),
        )
        if question_id in entry.answered_questions:
            return  # duplicate should never reach here; stay idempotent
        entry.answered_questions.add(question_id)
        entry.score += points
        if is_correct:
            entry.correct_answers += 1

    # -- serving ------------------------------------------------------------

    def serve(
        self,
        competition_id: str,
        limit: int = 100,
        participant_display_names: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        entries = list(self._board(competition_id).values())
        entries.sort(
            key=lambda e: (-e.score, -e.correct_answers, e.display_name.lower())
        )
        rows: list[dict[str, Any]] = []
        for index, entry in enumerate(entries[:limit], start=1):
            rows.append(
                {
                    "rank": index,
                    "participant_id": entry.participant_id,
                    "display_name": entry.display_name,
                    "score": entry.score,
                    "correct_answers": entry.correct_answers,
                    "answered_questions": len(entry.answered_questions),
                }
            )
        return rows

    def answered_questions(self, competition_id: str) -> dict[str, int]:
        """Map participant_id -> number of accepted answers (for the WS)."""
        return {
            pid: len(entry.answered_questions)
            for pid, entry in self._board(competition_id).items()
        }