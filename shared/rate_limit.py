from __future__ import annotations

import logging
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
_last_fallback_warning = 0.0
_fallback_warning_interval_seconds = 10.0

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, retry_after)
        super().__init__(f"Too many requests. Please retry in {self.retry_after} seconds.")


def _warn_memory_fallback(message: str) -> None:
    global _last_fallback_warning
    now = time.time()
    if now - _last_fallback_warning < _fallback_warning_interval_seconds:
        return
    _last_fallback_warning = now
    logger.warning("Rate limiter fallback to in-memory store: %s", message)


def trusted_client_identifier(
    *,
    x_real_ip: str | None = None,
    x_forwarded_for: str | None = None,
    remote_addr: str | None = None,
) -> str:
    real_ip = (x_real_ip or "").strip()
    if real_ip:
        return real_ip

    forwarded = (x_forwarded_for or "").strip()
    if forwarded:
        first_hop = forwarded.split(",")[0].strip()
        if first_hop:
            return first_hop

    direct = (remote_addr or "").strip()
    if direct:
        return direct

    return "unknown"


def _resolve_redis_url(redis_url: str | None) -> str:
    return (redis_url or os.getenv("REDIS_URL", "")).strip()


def _get_redis_client(redis_url: str | None) -> redis.Redis | None:
    global _redis_client
    global _redis_client_url
    global _redis_retry_after

    target_url = _resolve_redis_url(redis_url)
    if not target_url:
        _warn_memory_fallback("REDIS_URL is empty or missing.")
        return None

    now = time.time()
    if now < _redis_retry_after:
        _warn_memory_fallback("Redis temporarily unavailable.")
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
        _warn_memory_fallback("Redis ping failed or connection error.")
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
            _warn_memory_fallback("Redis command failed during rate-limit check.")
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
