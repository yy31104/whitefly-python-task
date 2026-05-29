from __future__ import annotations

import uuid

from shared.idempotency import (
    generate_idempotency_key,
    resolve_idempotency_key,
    sanitize_idempotency_key,
)


def test_generate_idempotency_key_returns_uuid4():
    key = generate_idempotency_key()

    parsed = uuid.UUID(key)
    assert parsed.version == 4


def test_sanitize_idempotency_key_accepts_safe_chars_and_max_length():
    assert sanitize_idempotency_key("abc-123_DEF.ghi:jkl") == "abc-123_DEF.ghi:jkl"
    assert sanitize_idempotency_key("x" * 64) == "x" * 64


def test_sanitize_idempotency_key_rejects_unsafe_values():
    assert sanitize_idempotency_key("") is None
    assert sanitize_idempotency_key("bad key") is None
    assert sanitize_idempotency_key("bad\nkey") is None
    assert sanitize_idempotency_key("x" * 65) is None


def test_resolve_idempotency_key_prefers_header():
    assert resolve_idempotency_key(header="header-key", form="form-key") == "header-key"
    assert resolve_idempotency_key(header="bad key", form="form-key") == "form-key"
    assert resolve_idempotency_key(header="", form="") is None
