"""Production-reliability middleware (S7).

One middleware assigns a request id, adopts a validated inbound correlation/request id,
binds the observability context, enforces a size limit and a per-request timeout,
applies a simple in-memory rate limit, records HTTP metrics, and returns a structured
error envelope (``{code, message, request_id}``) that never leaks a stack trace or PII.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import Settings
from app.core.context import ObservabilityContext, new_id, sanitise_id, use
from app.core.logging import get_logger
from app.observability.metrics import M_HTTP_LATENCY, M_HTTP_REQUESTS, registry

logger = get_logger("agentops.api")

_REQUEST_ID_HEADER = "X-Request-ID"
_CORRELATION_HEADER = "X-Correlation-ID"


class _RateLimiter:
    """A tiny fixed-window token bucket keyed by client identity (in-memory)."""

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float) -> bool:
        if self._per_minute <= 0:
            return True
        window = self._hits[key]
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self._per_minute:
            return False
        window.append(now)
        return True


class ReliabilityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings = settings
        self._limiter = _RateLimiter(settings.rate_limit_per_minute)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = sanitise_id(request.headers.get(_REQUEST_ID_HEADER)) or new_id(
            "req-"
        )
        correlation_id = (
            sanitise_id(request.headers.get(_CORRELATION_HEADER)) or request_id
        )
        context = ObservabilityContext(
            correlation_id=correlation_id, request_id=request_id
        )
        with use(context):
            # Request-size limit (declared Content-Length only; bodies are small here).
            length = request.headers.get("content-length")
            if length is not None and int(length) > self._settings.max_request_bytes:
                return self._envelope(413, "request_too_large", request_id)

            client = request.client.host if request.client else "unknown"
            if not self._limiter.allow(client, time.monotonic()):
                return self._envelope(429, "rate_limited", request_id)

            started = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    call_next(request),
                    timeout=self._settings.request_timeout_seconds,
                )
            except TimeoutError:
                return self._envelope(504, "request_timeout", request_id)
            except Exception:
                logger.exception("unhandled_request_error")
                return self._envelope(500, "internal_error", request_id)
            finally:
                elapsed = time.perf_counter() - started
                registry().observe(M_HTTP_LATENCY, elapsed, method=request.method)
            registry().inc(
                M_HTTP_REQUESTS,
                method=request.method,
                status=str(response.status_code),
            )
            response.headers[_REQUEST_ID_HEADER] = request_id
            response.headers[_CORRELATION_HEADER] = correlation_id
            return response

    @staticmethod
    def _envelope(status: int, code: str, request_id: str) -> JSONResponse:
        response = JSONResponse(
            status_code=status,
            content={
                "code": code,
                "message": code.replace("_", " "),
                "request_id": request_id,
            },
        )
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response
