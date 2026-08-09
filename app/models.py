"""Pydantic request/response models.

These models double as Swagger (/docs) documentation, so field descriptions
are kept meaningful. No model here carries a secret; correct answers are only
present in admin-facing models.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator, model_validator

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Enums / constants
# ---------------------------------------------------------------------------

CompetitionStatus = Literal[
    "draft", "scheduled", "waiting", "running", "paused", "finished", "cancelled"
]

QuestionType = Literal["mcq", "true_false", "text", "number", "audio"]

ALLOWED_QUESTION_TYPES = {"mcq", "true_false", "text", "number", "audio"}
ALLOWED_COMPETITION_STATUSES = {
    "draft",
    "scheduled",
    "waiting",
    "running",
    "paused",
    "finished",
    "cancelled",
}

_DISPLAY_NAME_RE = re.compile(r"^\S.{0,48}\S$|^\S$")
_CODE_RE = re.compile(r"^[A-Z0-9]{3,16}$")

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class CompetitionCreate(BaseModel):
    """Body for POST /api/admin/competitions."""

    name: str = Field(..., min_length=1, max_length=120, description="Competition display name.")
    description: str | None = Field(None, max_length=2000, description="Optional description.")
    code: str | None = Field(
        None,
        min_length=3,
        max_length=16,
        description="Short shareable code (A-Z, 0-9). Generated if omitted.",
    )
    status: CompetitionStatus = Field(
        "draft", description="Initial status (usually 'draft' or 'scheduled')."
    )
    scheduled_at: datetime | None = Field(
        None, description="Optional planned start time (UTC)."
    )
    default_points: int = Field(
        10, ge=0, le=10_000, description="Default points for a correct answer."
    )
    default_negative_points: int = Field(
        -2, ge=-10_000, le=0, description="Default penalty for a wrong answer."
    )
    speed_bonus_enabled: bool = Field(
        False, description="Optional speed bonus on correct answers (server-computed)."
    )

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if not _CODE_RE.match(code):
            raise ValueError("Code must be 3-16 characters (A-Z, 0-9).")
        return code


class CompetitionUpdate(BaseModel):
    """Optional fields an admin may update on a competition."""

    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    scheduled_at: datetime | None = None


class QuestionCreate(BaseModel):
    """Body for POST /api/admin/competitions/{id}/questions."""

    position: int = Field(..., ge=1, description="Order of the question within the competition.")
    text: str = Field(..., min_length=1, max_length=2000, description="Question text.")
    type: QuestionType = Field("mcq", description="Question type.")
    duration_seconds: int = Field(
        15, ge=1, le=600, description="Seconds allowed to answer (server-enforced)."
    )
    points: int | None = Field(
        None, ge=0, le=10_000, description="Points for a correct answer; falls back to the competition default."
    )
    negative_points: int | None = Field(
        None, ge=-10_000, le=0, description="Penalty for a wrong answer; falls back to the competition default."
    )
    explanation: str | None = Field(None, max_length=2000, description="Answer explanation shown after the question closes.")
    correct_answer_text: str | None = Field(
        None,
        description="Official answer for 'text'/'number' questions (never sent to participants).",
    )
    audio_url: str | None = Field(
        None, max_length=500, description="Optional audio file URL for 'audio' questions."
    )
    surah_number: int | None = Field(None, ge=1, le=114, description="Future: Quran surah number.")
    ayah_number: int | None = Field(None, ge=1, le=300, description="Future: Quran ayah number.")
    page_number: int | None = Field(None, ge=1, description="Future: Quran page number.")
    juz_number: int | None = Field(None, ge=1, le=30, description="Future: Quran juz number.")
    hizb_number: int | None = Field(None, ge=1, le=60, description="Future: Quran hizb number.")

    @model_validator(mode="after")
    def _validate_type_consistency(self) -> "QuestionCreate":
        if self.type in ("text", "number") and not (
            self.correct_answer_text is not None and self.correct_answer_text.strip()
        ):
            raise ValueError(
                "Questions of type 'text'/'number' require correct_answer_text."
            )
        if self.type == "audio" and not (self.audio_url and self.audio_url.strip()):
            raise ValueError("Questions of type 'audio' require audio_url.")
        return self


class QuestionUpdate(BaseModel):
    """Optional fields an admin may update on a question."""

    position: int | None = Field(None, ge=1)
    text: str | None = Field(None, min_length=1, max_length=2000)
    duration_seconds: int | None = Field(None, ge=1, le=600)
    points: int | None = Field(None, ge=0, le=10_000)
    negative_points: int | None = Field(None, ge=-10_000, le=0)
    explanation: str | None = Field(None, max_length=2000)
    correct_answer_text: str | None = Field(None, description="For 'text'/'number' questions.")


class ChoiceCreate(BaseModel):
    """Body for POST /api/admin/questions/{id}/choices."""

    text: str = Field(..., min_length=1, max_length=500, description="Choice text.")
    position: int = Field(..., ge=1, description="Display order (1..N).")
    is_correct: bool = Field(False, description="Whether this is the correct choice. Stored server-side only.")


class ChoiceUpdate(BaseModel):
    """Optional fields an admin may update on a choice."""

    text: str | None = Field(None, min_length=1, max_length=500)
    position: int | None = Field(None, ge=1)
    is_correct: bool | None = Field(None)


class JoinRequest(BaseModel):
    """Body for POST /api/competitions/join."""

    competition_code: str = Field(
        ..., min_length=3, max_length=16, description="Shareable competition code."
    )
    display_name: str = Field(
        ...,
        min_length=2,
        max_length=50,
        description="Public name shown to other participants.",
    )
    first_name: str | None = Field(None, max_length=60, description="Optional personal data (kept private).")
    last_name: str | None = Field(None, max_length=60, description="Optional personal data (kept private).")

    @field_validator("competition_code")
    @classmethod
    def _upper_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("display_name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not _DISPLAY_NAME_RE.match(cleaned):
            raise ValueError("Display name must be 2-50 characters.")
        return cleaned


class AnswerSubmission(BaseModel):
    """Body for POST /api/competitions/{id}/answers."""

    question_id: str = Field(..., description="UUID of the active question.")
    choice_id: str | None = Field(
        None, description="Selected choice UUID (required for mcq/true_false)."
    )
    answer_text: str | None = Field(
        None,
        max_length=1000,
        description="Free-text answer (required for text/number; the official answer is compared server-side).",
    )

    @model_validator(mode="after")
    def _require_one(self) -> "AnswerSubmission":
        if self.choice_id is None and (self.answer_text is None or not self.answer_text.strip()):
            raise ValueError("Provide either choice_id or answer_text.")
        return self


# ---------------------------------------------------------------------------
# Responses (safe subsets — never include correct answers for participants)
# ---------------------------------------------------------------------------


class APISuccess(BaseModel, Generic[T]):
    """Generic success envelope: {"success": true, "data": <payload>}."""

    success: Literal[True] = True
    data: T | None = None


class APIErrorBody(BaseModel):
    """Error details inside the failure envelope."""

    code: str = Field(..., description="Stable machine-readable error code.")
    message: str = Field(..., description="Human-readable error message.")


class APIFailure(BaseModel):
    """Generic failure envelope."""

    success: Literal[False] = False
    error: APIErrorBody


class ChoiceOut(BaseModel):
    """A choice visible to participants (no is_correct)."""

    id: str
    text: str
    position: int


class QuestionOut(BaseModel):
    """A question as broadcast to / fetched by participants."""

    id: str
    competition_id: str
    position: int
    text: str
    type: QuestionType
    duration_seconds: int
    points: int
    negative_points: int
    explanation: str | None = None
    audio_url: str | None = None
    surah_number: int | None = None
    ayah_number: int | None = None
    page_number: int | None = None
    juz_number: int | None = None
    hizb_number: int | None = None
    started_at: str | None = None
    ends_at: str | None = None
    choices: list[ChoiceOut] = Field(default_factory=list)


class ChoiceAdminOut(BaseModel):
    """A choice as visible to the admin (includes the correct flag)."""

    id: str
    question_id: str
    text: str
    position: int
    is_correct: bool


class QuestionAdminOut(BaseModel):
    """A question as visible to the admin (includes the official answer)."""

    id: str
    competition_id: str
    position: int
    text: str
    type: QuestionType
    duration_seconds: int
    points: int
    negative_points: int
    explanation: str | None = None
    correct_answer_text: str | None = None
    audio_url: str | None = None
    surah_number: int | None = None
    ayah_number: int | None = None
    page_number: int | None = None
    juz_number: int | None = None
    hizb_number: int | None = None
    choices: list[ChoiceAdminOut] = Field(default_factory=list)


class CompetitionOut(BaseModel):
    """A competition as returned to admins."""

    id: str
    code: str
    name: str
    description: str | None = None
    status: str
    scheduled_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    default_points: int
    default_negative_points: int
    speed_bonus_enabled: bool


class WaitroomInfo(BaseModel):
    """Waiting room payload for a participant."""

    competition_id: str
    competition_name: str
    participant_id: str
    participant_name: str
    participant_code: str
    competition_status: str
    connected_participants: int


class JoinResponse(BaseModel):
    """Response to POST /api/competitions/join."""

    competition_id: str
    competition_name: str
    competition_status: str
    participant_id: str
    participant_code: str
    display_name: str
    access_token: str = Field(
        ..., description="Opaque session token — send it as 'Authorization: Bearer <token>'."
    )
    connected_participants: int


class LeaderboardEntry(BaseModel):
    """One row of the leaderboard."""

    rank: int
    participant_id: str
    display_name: str
    score: float
    correct_answers: int
    answered_questions: int


class AnswerReceipt(BaseModel):
    """Feedback to a participant after submitting an answer."""

    accepted: bool
    is_correct: bool | None = None
    points: float | None = None
    response_time_ms: int | None = None
    explanation: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")