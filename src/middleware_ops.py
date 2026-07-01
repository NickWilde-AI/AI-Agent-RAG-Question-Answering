"""FastAPI 中间件：限流与请求观测。"""

from __future__ import annotations

import time
import threading
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from prometheus_client import Counter

from .config import SETTINGS
from .resilience import TokenBucket

RATE_LIMIT_REJECT = Counter("rag_rate_limit_total", "Rate limit rejections on /ask")

_BUCKETS = {}
_BUCKETS_LOCK = threading.Lock()
_MAX_BUCKETS = 10000


def _bucket_for(client_key: str) -> TokenBucket:
    with _BUCKETS_LOCK:
        bucket = _BUCKETS.get(client_key)
        if bucket is None:
            if len(_BUCKETS) >= _MAX_BUCKETS:
                # 演示实现使用有界内存；生产应替换为网关或 Redis 分布式限流。
                _BUCKETS.pop(next(iter(_BUCKETS)))
            bucket = TokenBucket(rate_per_sec=SETTINGS.rate_limit_rps, capacity=float(SETTINGS.rate_limit_burst))
            _BUCKETS[client_key] = bucket
        return bucket


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        protected = request.url.path in {"/ask","/ask/stream","/research/jobs","/agent-center/run"} or (
            request.method == "POST" and request.url.path.endswith("/documents")
        )
        if protected and SETTINGS.enable_rate_limit:
            client_key = request.client.host if request.client else "unknown"
            if not _bucket_for(client_key).allow():
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
