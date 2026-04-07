from __future__ import annotations

import logging

from redis.exceptions import RedisError

import shared.rate_limit as rate_limit


def test_rate_limit_logs_warning_when_falling_back_to_memory(caplog, monkeypatch):
    def fake_from_url(*args, **kwargs):
        raise RedisError("simulated redis outage")

    monkeypatch.setattr(rate_limit.redis.Redis, "from_url", fake_from_url)
    monkeypatch.setattr(rate_limit, "_redis_client", None)
    monkeypatch.setattr(rate_limit, "_redis_client_url", None)
    monkeypatch.setattr(rate_limit, "_redis_retry_after", 0.0)
    monkeypatch.setattr(rate_limit, "_last_fallback_warning", 0.0)
    monkeypatch.setattr(rate_limit, "_fallback_warning_interval_seconds", 0.0)

    with caplog.at_level(logging.WARNING):
        rate_limit.enforce_rate_limit(
            identifier="203.0.113.10",
            endpoint="test:endpoint",
            limit=10,
            window_seconds=60,
            redis_url="redis://redis:6379/0",
        )

    assert any(
        "Rate limiter fallback to in-memory store:" in record.message for record in caplog.records
    )
