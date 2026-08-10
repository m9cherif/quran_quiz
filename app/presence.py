"""Shared live-presence counter.

A participant counts as connected when it has an identified WebSocket in
this process OR polled the waiting room within PRESENCE_WINDOW seconds
(REST fallback — works even when WebSockets are blocked).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.config import PRESENCE_WINDOW
from app.database import fetch_many, parse_ts, utcnow


def count_connected_participants(competition_id: str) -> int:
    from app import context  # local import keeps this module dependency-free

    rows = fetch_many(
        "participants",
        conditions={"competition_id": competition_id},
        columns="id,last_seen_at",
    )
    if not rows:
        return 0
    cutoff = utcnow() - timedelta(seconds=PRESENCE_WINDOW)
    manager = context.manager
    count = 0
    for row in rows:
        if manager.is_identified(competition_id, row["id"]):
            count += 1
            continue
        seen = row.get("last_seen_at")
        if not seen:
            continue
        try:
            if parse_ts(seen) >= cutoff:
                count += 1
        except Exception:  # noqa: BLE001 — malformed timestamp → offline
            continue
    return count


def is_connected_row(row: dict[str, Any], competition_id: str) -> bool:
    """Per-participant version used by the admin participants list."""
    from app import context

    if context.manager.is_identified(competition_id, row["id"]):
        return True
    seen = row.get("last_seen_at")
    if not seen:
        return False
    try:
        cutoff = utcnow() - timedelta(seconds=PRESENCE_WINDOW)
        return parse_ts(seen) >= cutoff
    except Exception:  # noqa: BLE001 — malformed timestamp → offline
        return False