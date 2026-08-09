"""WebSocket connection management.

One WebSocket endpoint: /ws/competition/{competition_id}.
- Participants and admins connect, then send an `identify` message with their
  role and token before receiving anything.
- Messages are JSON. Dead connections are removed on send failure, and a
  periodic heartbeat sweeper purges connections that stopped responding.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import WebSocket

from app.config import settings

logger = logging.getLogger("quran_quiz.ws")

Role = Literal["admin", "participant"]

HEARTBEAT_INTERVAL = 30.0
HEARTBEAT_TIMEOUT = 90.0

# WS event types (documented in README).
EVENT_COMPETITION_STATE = "competition_state"
EVENT_PARTICIPANT_JOINED = "participant_joined"
EVENT_PARTICIPANT_LEFT = "participant_left"
EVENT_QUESTION_STARTED = "question_started"
EVENT_QUESTION_ENDED = "question_ended"
EVENT_ANSWER_RECEIVED = "answer_received"
EVENT_LEADERBOARD_UPDATED = "leaderboard_updated"
EVENT_COMPETITION_STARTED = "competition_started"
EVENT_COMPETITION_PAUSED = "competition_paused"
EVENT_COMPETITION_RESUMED = "competition_resumed"
EVENT_COMPETITION_FINISHED = "competition_finished"
EVENT_ERROR = "error"
EVENT_PING = "ping"
EVENT_PONG = "pong"
EVENT_IDENTIFY_REQUIRED = "identify_required"


@dataclass(eq=False)
class Connection:
    websocket: WebSocket
    role: Role = "participant"
    participant_id: str | None = None
    participant_name: str | None = None
    identified: bool = False
    last_activity: float = field(default_factory=time.monotonic)


class ConnectionManager:
    """Registry of live WebSocket connections per competition."""

    def __init__(self) -> None:
        self._connections: dict[str, set[Connection]] = {}

    # -- lifecycle ---------------------------------------------------------

    def add(self, competition_id: str, connection: Connection) -> None:
        self._connections.setdefault(competition_id, set()).add(connection)

    def remove(self, competition_id: str, connection: Connection) -> None:
        bucket = self._connections.get(competition_id)
        if bucket:
            bucket.discard(connection)
            if not bucket:
                self._connections.pop(competition_id, None)

    def count_identified(self, competition_id: str) -> int:
        bucket = self._connections.get(competition_id)
        if not bucket:
            return 0
        return sum(
            1 for c in bucket if c.identified and c.role == "participant"
        )

    # -- sends --------------------------------------------------------------

    async def send_json(self, connection: Connection, message: dict[str, Any]) -> bool:
        """Send a message; return False and drop the connection on failure."""
        try:
            await connection.websocket.send_json(message)
            return True
        except Exception:  # noqa: BLE001 — connection is dead
            logger.info(
                "Dropping dead WebSocket (competition role=%s)", connection.role
            )
            return False

    async def broadcast(
        self,
        competition_id: str,
        message: dict[str, Any],
        roles: set[Role] | None = None,
        exclude_connection: Connection | None = None,
    ) -> None:
        """Send a JSON message to a subset of connections."""
        bucket = self._connections.get(competition_id)
        if not bucket:
            return
        dead: list[Connection] = []
        for connection in list(bucket):
            if exclude_connection is connection:
                continue
            if roles is not None and connection.role not in roles:
                continue
            if not connection.identified:
                continue
            if not await self.send_json(connection, message):
                dead.append(connection)
        for connection in dead:
            self.remove(competition_id, connection)

    async def broadcast_all(
        self, competition_id: str, message: dict[str, Any]
    ) -> None:
        await self.broadcast(competition_id, message, roles=None)

    # -- heartbeat ----------------------------------------------------------

    async def heartbeat_loop(self) -> None:
        """Periodically ping every connection; purge silent ones."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            now = time.monotonic()
            for competition_id, bucket in list(self._connections.items()):
                for connection in list(bucket):
                    if not connection.identified:
                        if now - connection.last_activity > HEARTBEAT_TIMEOUT:
                            self.remove(competition_id, connection)
                        continue
                    if now - connection.last_activity > HEARTBEAT_TIMEOUT:
                        logger.info(
                            "Purging unresponsive WebSocket (competition=%s)",
                            competition_id,
                        )
                        self.remove(competition_id, connection)
                        continue
                    await self.send_json(
                        connection, {"type": EVENT_PING, "at": _now_iso()}
                    )

    async def mark_activity(self, connection: Connection) -> None:
        connection.last_activity = time.monotonic()


def _now_iso() -> str:
    from app.database import iso, utcnow

    return iso(utcnow())


# ---------------------------------------------------------------------------
# Identify flow
# ---------------------------------------------------------------------------


def validate_identify(message: dict[str, Any]) -> tuple[Role, str] | None:
    """Validate an `identify` message.

    Returns (role, token) or None when malformed.
    """
    if not isinstance(message, dict):
        return None
    if message.get("type") != "identify":
        return None
    role = message.get("role")
    token = message.get("token")
    if role not in ("admin", "participant") or not isinstance(token, str) or not token:
        return None
    return role, token


def is_admin_token(token: str) -> bool:
    import secrets

    return secrets.compare_digest(token.encode(), settings.admin_api_key.encode())