"""限流、熔断与 Router 降级工具。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TokenBucket:
    """简单令牌桶，用于 /ask 入口限流。"""

    rate_per_sec: float
    capacity: float
    _tokens: float = field(init=False)
    _last: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._tokens = self.capacity

    def allow(self, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            elapsed = max(0.0, now - self._last)
            self._last = now
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_sec)
            if self._tokens >= cost:
                self._tokens -= cost
                return True
            return False


@dataclass
class CircuitBreaker:
    """
    简易熔断器：连续失败后在一段时间内走 open 状态（调用方应降级）。
    """

    failure_threshold: int = 5
    recovery_seconds: float = 30.0
    _failures: int = 0
    _opened_at: Optional[float] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if time.monotonic() - self._opened_at >= self.recovery_seconds:
                self._opened_at = None
                self._failures = 0
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold and self._opened_at is None:
                self._opened_at = time.monotonic()
