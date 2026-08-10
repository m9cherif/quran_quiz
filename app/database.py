"""Supabase access layer.

The server talks to Supabase PostgreSQL through the official SDK.
- The service-role client performs ALL server-side operations; all payloads are
  validated by this server first, so elevated privileges never leak to clients.
- The anon key is kept in settings only, reserved for a future browser-facing
  model; it is never exposed by this server.

Supabase PostgREST does not support parameterized identifiers, so table and
column names passed here are restricted to a hard-coded allow-list.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from supabase import Client, create_client

from app.config import settings
from app.errors import APIError

logger = logging.getLogger("quran_quiz.database")

# Hard-coded allow-list of table names accessible through this module.
_ALLOWED_TABLES: frozenset[str] = frozenset(
    {
        "competitions",
        "participants",
        "questions",
        "choices",
        "answers",
        "admin_keys",
    }
)

_TABLE_LABELS: dict[str, str] = {
    "competitions": "COMPETITION_NOT_FOUND",
    "participants": "PARTICIPANT_NOT_FOUND",
}

_service_client: Client | None = None


def get_client() -> Client:
    """Return the lazily-created service-role Supabase client."""
    global _service_client
    if _service_client is None:
        _service_client = create_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
    return _service_client


def healthcheck_database() -> None:
    """Run a cheap read to prove the Supabase connection works.

    Raises APIError(DATABASE_ERROR) when the database is unreachable.
    """
    try:
        # No data is read; a failed connection raises here either way.
        get_client().table("competitions").select("id").limit(1).execute()
    except Exception as exc:  # noqa: BLE001 — logged, never leaked
        logger.error("Database connectivity check failed: %s", type(exc).__name__)
        raise APIError(
            "DATABASE_ERROR", "Database is unreachable.", status_code=503
        ) from exc


def _check_table(table: str) -> None:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Table not allowed: {table}")


def fetch_one(
    table: str,
    conditions: Mapping[str, Any],
    columns: str = "*",
    ilike: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Fetch a single row matching all conditions, or None.

    `ilike` adds case-insensitive equality filters (e.g. display_name).
    """
    _check_table(table)
    try:
        query = get_client().table(table).select(columns)
        for key, value in conditions.items():
            query = query.eq(key, value)
        for key, value in (ilike or {}).items():
            query = query.ilike(key, value)
        result = query.maybe_single().execute()
        data = result.data
        return dict(data) if isinstance(data, dict) else None
    except APIError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "DATABASE_ERROR fetch_one(%s): %s — %s", table, type(exc).__name__, exc
        )
        raise _db_error(table, exc) from exc


def fetch_many(
    table: str,
    conditions: Mapping[str, Any] | None = None,
    columns: str = "*",
    order: str | None = None,
    ascending: bool = True,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch many rows, optionally filtered/ordered."""
    _check_table(table)
    try:
        query = get_client().table(table).select(columns)
        if conditions:
            for key, value in conditions.items():
                query = query.eq(key, value)
        if order:
            query = query.order(order, desc=not ascending)
        if limit is not None:
            query = query.limit(limit)
        result = query.execute()
        return [dict(row) for row in (result.data or [])]
    except APIError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "DATABASE_ERROR fetch_many(%s): %s — %s", table, type(exc).__name__, exc
        )
        raise _db_error(table, exc) from exc


def insert_one(table: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Insert one row and return the server-generated row."""
    _check_table(table)
    try:
        result = get_client().table(table).insert(dict(payload)).execute()
        rows = result.data or []
        if not rows:
            raise APIError("DATABASE_ERROR", "Insert returned no row.")
        return dict(rows[0])
    except APIError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "DATABASE_ERROR insert_one(%s): %s — %s", table, type(exc).__name__, exc
        )
        raise _db_error(table, exc) from exc


def update_one(
    table: str,
    conditions: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Update rows matching all conditions; return the first updated row."""
    _check_table(table)
    try:
        query = get_client().table(table).update(dict(payload))
        for key, value in conditions.items():
            query = query.eq(key, value)
        result = query.execute()
        rows = result.data or []
        return dict(rows[0]) if rows else None
    except APIError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "DATABASE_ERROR update_one(%s): %s — %s", table, type(exc).__name__, exc
        )
        raise _db_error(table, exc) from exc


def delete_many(
    table: str, conditions: Mapping[str, Any], limit: int | None = None
) -> int:
    """Delete rows matching all conditions; return the number deleted."""
    _check_table(table)
    try:
        query = get_client().table(table).delete()
        for key, value in conditions.items():
            query = query.eq(key, value)
        if limit is not None:
            query = query.limit(limit)
        result = query.execute()
        return len(result.data or [])
    except APIError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "DATABASE_ERROR delete_many(%s): %s — %s", table, type(exc).__name__, exc
        )
        raise _db_error(table, exc) from exc


def _db_error(table: str, exc: Exception | None = None) -> APIError:
    if table == "admin_keys":
        return APIError(
            "DATABASE_ERROR",
            "Admin key store is not set up: run the admin_keys block of "
            "supabase/schema.sql in the Supabase SQL editor.",
            status_code=500,
        )
    if exc is not None:
        detail = str(exc)
        if "could not find" in detail.lower() or "schema cache" in detail.lower():
            return APIError(
                "DATABASE_ERROR",
                f"Database table '{table}' is missing from the Supabase "
                "project — re-run supabase/schema.sql in the Supabase SQL "
                "editor (it is idempotent).",
                status_code=404,
            )
    label = _TABLE_LABELS.get(table, "DATABASE_ERROR")
    hint = ""
    if exc is not None:
        excerpt = " — ".join(str(exc).splitlines())[:160]
        if excerpt:
            hint = f" ({excerpt}…)"
    message = (
        "The requested resource could not be loaded."
        if label != "DATABASE_ERROR"
        else "A database error occurred."
    )
    return APIError(
        label,
        message + hint,
        status_code=404 if label != "DATABASE_ERROR" else 500,
    )


# ---------------------------------------------------------------------------
# Time helpers — the server is the only time authority (UTC everywhere).
# ---------------------------------------------------------------------------


def utcnow() -> datetime:
    """Current UTC time with timezone info."""
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    """ISO-8601 UTC string (e.g. '2026-08-09T12:00:00Z') for API payloads."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp returned by Supabase."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def unique_value(
    table: str,
    column: str,
    generate: Any,
    conditions: Mapping[str, Any] | None = None,
    attempts: int = 5,
) -> str:
    """Generate a unique short code with retry on collision."""
    _check_table(table)
    for _ in range(attempts):
        value = generate()
        extra = {**(conditions or {}), column: value}
        if fetch_one(table, extra, columns="id") is None:
            return value
    raise APIError("DATABASE_ERROR", "Could not generate a unique code.")


def serialize_row(row: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    """Pick a safe subset of a DB row for API responses."""
    return {field: row.get(field) for field in fields}


def is_unique_violation(exc: Exception) -> bool:
    """True when a DB exception is a PostgreSQL unique-constraint violation."""
    message = str(exc)
    return "unique constraint" in message.lower() or "23505" in message