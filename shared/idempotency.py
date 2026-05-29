from __future__ import annotations

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
