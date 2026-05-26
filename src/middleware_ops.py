"""FastAPI 中间件：限流与请求观测。"""

from __future__ import annotations

import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from prometheus_client import Counter

from .config import SETTINGS
from .resilience import TokenBucket

RATE_LIMIT_REJECT = Counter("rag_rate_limit_total", "Rate limit rejections on /ask")

_ASK_BUCKET = TokenBucket(
    rate_per_sec=SETTINGS.rate_limit_rps,
    capacity=float(SETTINGS.rate_limit_burst),
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path == "/ask" and SETTINGS.enable_rate_limit:
            if not _ASK_BUCKET.allow():
                RATE_LIMIT_REJECT.inc()
                return Response(
                    content='{"detail":"rate limit exceeded, retry later"}',
                    status_code=429,
                    media_type="application/json",
                )
        return await call_next(request)


class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        t0 = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
        return response
