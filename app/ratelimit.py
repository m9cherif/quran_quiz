"""Lightweight in-memory fixed-window rate limiter.

No external dependency: a windowed deque per (scope, identity) with periodic
garbage collection of stale entries. Suitable for single-process deployments.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable

from fastapi import Request

from app.errors import APIError

logger = logging.getLogger("quran_quiz.ratelimit")

DEFAULT_MAX_REQUESTS = 60
DEFAULT_WINDOW_SECONDS = 60


@dataclass
class _Window:
    max_requests: int
    window_seconds: float
    hits: deque[float] = field(default_factory=deque)

    def allow(self) -> tuple[bool, int]:
        now = time.monotonic()
        while self.hits and now - self.hits[0] > self.window_seconds:
            self.hits.popleft()
        if len(self.hits) >= self.max_requests:
            return False, self.max_requests
        self.hits.append(now)
        return True, len(self.hits)


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], _Window] = defaultdict(
            lambda: _Window(DEFAULT_MAX_REQUESTS, DEFAULT_WINDOW_SECONDS)
        )
        self._last_gc = time.monotonic()
        self._gc_every = 60.0

    def check(
        self,
        scope: str,
        identity: str,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._maybe_gc()
        key = (scope, identity)
        window = self._buckets.get(key)
        if window is None:
            window = _Window(max_requests, window_seconds)
            self._buckets[key] = window
        elif (
            window.max_requests != max_requests
            or window.window_seconds != window_seconds
        ):
            window = _Window(max_requests, window_seconds)
            self._buckets[key] = window
        allowed, _ = window.allow()
        if not allowed:
            logger.warning("Rate limit exceeded for scope=%s", scope)
            raise APIError(
                "RATE_LIMIT_EXCEEDED",
                "Too many requests. Please slow down.",
                status_code=429,
            )

    def _maybe_gc(self) -> None:
        now = time.monotonic()
        if now - self._last_gc < self._gc_every:
            return
        self._last_gc = now
        stale = [
            key
            for key, window in self._buckets.items()
            if not window.hits or now - window.hits[0] > window.window_seconds
        ]
        for key in stale:
            del self._buckets[key]


limiter = RateLimiter()


def rate_limit(
    max_requests: int = DEFAULT_MAX_REQUESTS,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> Callable[[Request], None]:
    """Dependency factory: apply the fixed-window limit keyed by client IP."""

    def _check(request: Request) -> None:
        identity = request.client.host if request.client else "unknown"
        limiter.check("http", identity, max_requests, window_seconds)

    return _check