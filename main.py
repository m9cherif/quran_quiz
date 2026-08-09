"""FastAPI entry point.

Run with:
    uvicorn main:app --reload
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import context
from app.config import settings
from app.database import healthcheck_database
from app.errors import register_exception_handlers
from app.security import key_store
from app.routers import admin, health, keys, pages, participants, ws

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("quran_quiz.main")

MAX_BODY_BYTES = 64 * 1024  # 64 KB


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before they reach the handlers."""

    def __init__(self, app: FastAPI, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length is not None:
            try:
                if int(length) > self.max_bytes:
                    from fastapi.responses import JSONResponse

                    response = JSONResponse(
                        status_code=413,
                        content={
                            "success": False,
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": f"Request body too large (max {self.max_bytes} bytes).",
                            },
                        },
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass

        async def wrapped_receive():
            message = await receive()
            if message["type"] == "http.request" and message.get("body"):
                message = dict(message)
                message["body"] = message["body"][: self.max_bytes]
            return message

        await self.app(scope, wrapped_receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: verify Supabase, restore game state, launch background loops."""
    logger.info("SERVER_STARTED service=%s", "quran-competition-server")
    try:
        healthcheck_database()
        logger.info("SUPABASE_CONNECTED")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Supabase unreachable at startup (%s); /health/database will report it.",
            type(exc).__name__,
        )

    context.game.restore_active_questions()
    await key_store.refresh()

    clock_task = asyncio.create_task(context.game.clock_loop())
    heartbeat_task = asyncio.create_task(context.manager.heartbeat_loop())
    logger.info("BACKGROUND_TASKS_STARTED")
    try:
        yield
    finally:
        clock_task.cancel()
        heartbeat_task.cancel()
        for task in (clock_task, heartbeat_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("SERVER_STOPPED")


app = FastAPI(
    title="Quran Competition Server",
    description=(
        "Real-time quiz competition backend. Admin routes require "
        "`Authorization: Bearer <ADMIN_API_KEY>`; participant routes require "
        "the opaque token issued by POST /api/competitions/join. "
        "The server is the only authority on time and scoring."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BODY_BYTES)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(health.router)
app.include_router(pages.router)
app.include_router(keys.router)
app.include_router(admin.router)
app.include_router(participants.router)
app.include_router(ws.router)


@app.get("/api")
async def api_banner() -> dict[str, str]:
    """API banner (the root page is the web app)."""
    return {
        "service": "quran-competition-server",
        "docs": "/docs",
        "health": "/health",
    }