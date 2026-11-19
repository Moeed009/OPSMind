"""Per-provider and per-user Redis rate limiters with in-memory fallback for tests."""
from __future__ import annotations

import threading
import time
from collections import defaultdict

from app.ai.exceptions import AIRateLimitError


class RedisRateLimiter:
    """Fixed-minute sliding/window counter backed by Redis (falls back to in-memory)."""

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client
        self._lock = threading.Lock()
        self._mem_counts: dict[str, int] = defaultdict(int)

    def _check_memory(self, key: str, limit: int) -> None:
        with self._lock:
            self._mem_counts[key] += 1
            if self._mem_counts[key] > limit:
                raise AIRateLimitError(
                    f"Rate limit exceeded for {key}",
                    retry_after_seconds=60.0,
                )

    def acquire(self, scope: str, limit_per_minute: int) -> None:
        if limit_per_minute <= 0:
            return
        bucket = int(time.time() // 60)
        key = f"ai:rl:{scope}:{bucket}"
        if self._redis is None:
            self._check_memory(key, limit_per_minute)
            return
        try:
            count = int(self._redis.incr(key))
            if count == 1:
                self._redis.expire(key, 65)
            if count > limit_per_minute:
                raise AIRateLimitError(
                    f"Rate limit exceeded for {scope}",
                    retry_after_seconds=60.0,
                )
        except AIRateLimitError:
            raise
        except Exception:
            self._check_memory(key, limit_per_minute)
