from __future__ import annotations

import hashlib
import json
import re
import uuid

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
