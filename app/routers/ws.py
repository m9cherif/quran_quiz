"""Real-time WebSocket endpoint.

Flow: connect → receive `{"type":"identify","role":"admin"|"participant","token":...}`
→ receive event broadcasts. Heartbeat: the server pings every 30s; any incoming
message marks the connection alive; silent connections are purged.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import context
from app.database import update_one, utcnow, iso
from app.errors import APIError
from app.ratelimit import limiter
from app.security import resolve_participant_row
from app.ws_manager import (
    EVENT_COMPETITION_STATE,
    EVENT_ERROR,
    EVENT_IDENTIFY_REQUIRED,
    EVENT_PARTICIPANT_JOINED,
    EVENT_PARTICIPANT_LEFT,
    EVENT_PONG,
    Connection,
    ConnectionManager,
    is_admin_token,
    validate_identify,
)

logger = logging.getLogger("quran_quiz.ws_endpoint")

router = APIRouter(tags=["websocket"])


def _admin_state_payload(competition_id: str) -> dict[str, Any]:
    from app.database import fetch_one

    competition = fetch_one("competitions", {"id": competition_id}) or {}
    state = context.game.get_state(competition_id)
    active: dict[str, Any] | None = None
    if state is not None and state.question_id and not state.question_ended_flag:
        active = {
            "question_id": state.question_id,
            "position": state.question_position,
            "started_at": iso(state.started_at) if state.started_at else None,
            "ends_at": iso(state.ends_at) if state.ends_at else None,
            "paused": state.paused,
        }
    return {
        "type": EVENT_COMPETITION_STATE,
        "competition_id": competition_id,
        "status": competition.get("status", "draft"),
        "active_question": active,
        "participants_connected": context.manager.count_identified(
            competition_id
        ),
    }


def _participant_state_payload(
    competition_id: str, participant: dict[str, Any]
) -> dict[str, Any]:
    from app.database import fetch_one

    competition = fetch_one("competitions", {"id": competition_id}) or {}
    state = context.game.get_state(competition_id)
    active: dict[str, Any] | None = None
    if state is not None and state.question_id and not state.question_ended_flag:
        active = {
            "question_id": state.question_id,
            "position": state.question_position,
            "started_at": iso(state.started_at) if state.started_at else None,
            "ends_at": iso(state.ends_at) if state.ends_at else None,
        }
    return {
        "type": EVENT_COMPETITION_STATE,
        "competition_id": competition_id,
        "status": competition.get("status", "draft"),
        "participant_id": participant["id"],
        "active_question": active,
        "participants_connected": context.manager.count_identified(
            competition_id
        ),
    }


@router.websocket("/ws/competition/{competition_id}")
async def competition_websocket(
    websocket: WebSocket, competition_id: str
) -> None:
    await websocket.accept()
    manager: ConnectionManager = context.manager
    connection = Connection(websocket)
    manager.add(competition_id, connection)

    client_host = websocket.client.host if websocket.client else "unknown"
    await websocket.send_json(
        {
            "type": EVENT_IDENTIFY_REQUIRED,
            "message": "Send {\"type\":\"identify\",\"role\":\"admin\"|\"participant\",\"token\":\"...\"}",
        }
    )

    try:
        limiter.check("ws", client_host, max_requests=20, window_seconds=60)
    except APIError as exc:
        await websocket.send_json(
            {"type": EVENT_ERROR, "code": exc.code, "message": exc.message}
        )
        await websocket.close(code=1008)
        manager.remove(competition_id, connection)
        return

    try:
        while True:
            data = await websocket.receive_json()
            await manager.mark_activity(connection)

            if data.get("type") == "ping":
                await websocket.send_json({"type": EVENT_PONG, "at": iso(utcnow())})
                continue
            if not connection.identified:
                await _handle_identify(
                    websocket, manager, competition_id, connection, data
                )
                continue
            if data.get("type") == "pong":
                continue
            await websocket.send_json(
                {
                    "type": EVENT_ERROR,
                    "code": "INVALID_REQUEST",
                    "message": "Unknown message type.",
                }
            )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "WebSocket closed unexpectedly (competition=%s): %s",
            competition_id,
            type(exc).__name__,
        )
        try:
            await websocket.close(code=1011)
        except Exception:  # noqa: BLE001 — already gone
            pass
    finally:
        await _handle_disconnect(manager, competition_id, connection)


async def _handle_identify(
    websocket: WebSocket,
    manager: ConnectionManager,
    competition_id: str,
    connection: Connection,
    data: Any,
) -> None:
    identified = validate_identify(data)
    if identified is None:
        await websocket.send_json(
            {
                "type": EVENT_ERROR,
                "code": "INVALID_REQUEST",
                "message": "Identify with {\"type\":\"identify\",\"role\":...,\"token\":...} first.",
            }
        )
        return
    role, token = identified

    if role == "admin":
        if not is_admin_token(token):
            await websocket.send_json(
                {
                    "type": EVENT_ERROR,
                    "code": "NOT_AUTHORIZED",
                    "message": "Invalid admin token.",
                }
            )
            return
        connection.role = "admin"
        connection.identified = True
        await websocket.send_json(_admin_state_payload(competition_id))
        logger.info("ADMIN_CONNECTED competition=%s", competition_id)
        return

    try:
        participant = resolve_participant_row(competition_id, token)
    except APIError as exc:
        await websocket.send_json(
            {"type": EVENT_ERROR, "code": exc.code, "message": exc.message}
        )
        return
    connection.role = "participant"
    connection.identified = True
    connection.participant_id = participant["id"]
    connection.participant_name = participant["display_name"]
    await manager.broadcast_all(
        competition_id,
        {
            "type": EVENT_PARTICIPANT_JOINED,
            "competition_id": competition_id,
            "display_name": participant["display_name"],
            "participant_code": participant.get("participant_code"),
        },
    )
    update_one(
        "participants",
        {"id": participant["id"]},
        {"connected": True, "last_seen_at": iso(utcnow())},
    )
    logger.info(
        "PARTICIPANT_CONNECTED competition=%s participant=%s",
        competition_id,
        participant["id"],
    )
    await websocket.send_json(
        _participant_state_payload(competition_id, participant)
    )


async def _handle_disconnect(
    manager: ConnectionManager,
    competition_id: str,
    connection: Connection,
) -> None:
    manager.remove(competition_id, connection)
    if not connection.identified or connection.role != "participant":
        return
    if connection.participant_id is not None:
        update_one(
            "participants",
            {"id": connection.participant_id},
            {"connected": False, "last_seen_at": iso(utcnow())},
        )
    logger.info(
        "PARTICIPANT_DISCONNECTED competition=%s participant=%s",
        competition_id,
        connection.participant_id,
    )
    await manager.broadcast_all(
        competition_id,
        {
            "type": EVENT_PARTICIPANT_LEFT,
            "competition_id": competition_id,
            "display_name": connection.participant_name,
        },
    )