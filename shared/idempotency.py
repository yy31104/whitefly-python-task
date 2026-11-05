from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta

_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def generate_idempotency_key() -> str:
    return str(uuid.uuid4())


def sanitize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        return None
    return value


def resolve_idempotency_key(*, header: str | None, form: str | None) -> str | None:
    return sanitize_idempotency_key(header) or sanitize_idempotency_key(form)


def request_fingerprint(validated_payload: dict[str, str]) -> str:
    """SHA-256 of the canonical submission fields (no PII is stored, only the hash).

    The fields match `_payload_matches` in shared/services.py: first name, last
    name and email. The honeypot is excluded because it is always empty after
    validation.
    """
    canonical = json.dumps(
        {
            "first_name": validated_payload["first_name"],
            "last_name": validated_payload["last_name"],
            "email": validated_payload["email"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


DEFAULT_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


def idempotency_ttl() -> timedelta:
    """How long a key stays bound to its first request (``IDEMPOTENCY_KEY_TTL_SECONDS``).

    After this the key is free again: a later request with it is a new request.
    A missing, non-numeric or non-positive value falls back to 24 hours.
    """
    raw = os.getenv("IDEMPOTENCY_KEY_TTL_SECONDS", "")
    try:
        seconds = int(raw)
    except ValueError:
        seconds = DEFAULT_IDEMPOTENCY_TTL_SECONDS
    if seconds <= 0:
        seconds = DEFAULT_IDEMPOTENCY_TTL_SECONDS
    return timedelta(seconds=seconds)


def is_expired(created_at: datetime | None, *, now: datetime | None = None) -> bool:
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        # SQLite returns naive datetimes; the application always writes UTC.
        created_at = created_at.replace(tzinfo=UTC)
    return created_at <= (now or datetime.now(UTC)) - idempotency_ttl()


def scope_idempotency_key(key: str | None, client_scope: str | None) -> str | None:
    """Bind a client-supplied key to the client that sent it.

    Two clients that happen to pick the same key no longer collide. The stored
    value is SHA-256 hex of ``client_scope`` and the key, 64 characters, which
    fits the existing columns and the key pattern, so no migration is needed and
    neither the raw key nor the client identifier is stored.

    ``client_scope=None`` keeps the key unscoped (direct service and worker calls).
    """
    if not key:
        return None
    if client_scope is None:
        return key
    material = f"{client_scope}\0{key}".encode()
    return hashlib.sha256(material).hexdigest()
