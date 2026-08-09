"""Unified API error type and FastAPI exception handlers.

Every error returned to a client uses the same shape:

    {"success": false, "error": {"code": "...", "message": "..."}}

Python stack traces are never sent to clients.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("quran_quiz.errors")

# Stable error codes exposed through the API.
ERROR_CODES = frozenset(
    {
        "COMPETITION_NOT_FOUND",
        "COMPETITION_NOT_RUNNING",
        "COMPETITION_NOT_ACCEPTING_PARTICIPANTS",
        "QUESTION_NOT_ACTIVE",
        "QUESTION_EXPIRED",
        "ALREADY_ANSWERED",
        "INVALID_ANSWER",
        "NOT_AUTHORIZED",
        "PARTICIPANT_NOT_FOUND",
        "DATABASE_ERROR",
        "INVALID_REQUEST",
        "RATE_LIMIT_EXCEEDED",
        "INTERNAL_SERVER_ERROR",
    }
)


class APIError(Exception):
    """Application error mapped 1:1 to the JSON error envelope."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"Unknown error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def ok(data: Any = None) -> dict[str, Any]:
    """Standard success envelope: {"success": true, "data": ...}."""
    return {"success": True, "data": data}


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = first.get("msg", "Request validation failed")
        detail = f"{loc}: {message}" if loc else message
        return JSONResponse(
            status_code=422,
            content=_error_payload("INVALID_REQUEST", f"Validation error — {detail}"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        if exc.status_code == 404:
            code, message = "INVALID_REQUEST", "Resource not found."
        elif exc.status_code == 405:
            code, message = "INVALID_REQUEST", "Method not allowed."
        else:
            code, message = "INVALID_REQUEST", str(exc.detail or "Request failed.")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(code, message),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        # Log the real exception server-side; never leak it to the client.
        logger.exception("Unhandled exception: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                "INTERNAL_SERVER_ERROR", "An unexpected error occurred."
            ),
        )