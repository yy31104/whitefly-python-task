from __future__ import annotations

import os
import time
from threading import Lock

import redis
from redis.exceptions import RedisError

_memory_buckets: dict[str, tuple[int, float]] = {}
_memory_lock = Lock()

_redis_client: redis.Redis | None = None
_redis_client_url: str | None = None
_redis_retry_after = 0.0


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, retry_after)
        super().__init__(f"Too many requests. Please retry in {self.retry_after} seconds.")


def _resolve_redis_url(redis_url: str | None) -> str:
    return (redis_url or os.getenv("REDIS_URL", "")).strip()


def _get_redis_client(redis_url: str | None) -> redis.Redis | None:
    global _redis_client
    global _redis_client_url
    global _redis_retry_after

    target_url = _resolve_redis_url(redis_url)
    if not target_url:
        return None

    now = time.time()
    if now < _redis_retry_after:
        return None

    if _redis_client is not None and _redis_client_url == target_url:
        return _redis_client

    try:
        candidate = redis.Redis.from_url(
            target_url,
            decode_responses=True,
            socket_timeout=1,
            socket_connect_timeout=1,
        )
        candidate.ping()
    except RedisError:
        _redis_client = None
        _redis_client_url = None
        _redis_retry_after = now + 2
        return None

    _redis_client = candidate
    _redis_client_url = target_url
    _redis_retry_after = 0.0
    return _redis_client


def enforce_rate_limit(
    *,
    identifier: str,
    endpoint: str,
    limit: int,
    window_seconds: int,
    redis_url: str | None = None,
) -> None:
    if limit <= 0:
        return

    safe_identifier = identifier.strip() or "unknown"
    key = f"rate_limit:{endpoint}:{safe_identifier}"

    client = _get_redis_client(redis_url)
    if client is not None:
        try:
            current = int(client.incr(key))
            if current == 1:
                client.expire(key, window_seconds)

            ttl = int(client.ttl(key))
            if current > limit:
                raise RateLimitExceeded(retry_after=max(ttl, 1))
            return
        except RedisError:
            pass

    now = time.time()
    with _memory_lock:
        current, reset_at = _memory_buckets.get(key, (0, now + window_seconds))
        if now >= reset_at:
            current = 0
            reset_at = now + window_seconds

        current += 1
        _memory_buckets[key] = (current, reset_at)

        if current > limit:
            retry_after = max(int(reset_at - now), 1)
            raise RateLimitExceeded(retry_after=retry_after)
