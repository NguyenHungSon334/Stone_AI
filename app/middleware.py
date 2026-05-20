"""
Per-user sliding-window rate limiter and request-ID middleware.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


# ---------------------------------------------------------------------------
# Sliding-window rate limiter
# ---------------------------------------------------------------------------

class _UserRateLimiter:
    """
    Thread-safe* sliding-window rate limiter keyed by an arbitrary string.

    *FastAPI runs in a single async event loop; no threading needed.
     asyncio is cooperative so no lock is required for deque operations.
    """

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self._max = max_calls
        self._window = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        dq = self._buckets[key]

        # Evict timestamps outside the window
        while dq and dq[0] < cutoff:
            dq.popleft()

        if len(dq) >= self._max:
            return False

        dq.append(now)
        return True


# Singleton — shared across all requests
_rate_limiter: _UserRateLimiter | None = None


def get_rate_limiter() -> _UserRateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = _UserRateLimiter(
            max_calls=settings.rate_limit_per_minute,
            window_seconds=60,
        )
    return _rate_limiter


def is_rate_limited(sender_id: str) -> bool:
    return not get_rate_limiter().allow(sender_id)


# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------

class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-ID to every request/response for tracing."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
