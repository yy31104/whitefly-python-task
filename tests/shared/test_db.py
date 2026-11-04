from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from shared.db import _build_engine

SENSITIVE_EMAIL = "leak@example.com"
SENSITIVE_FIRST_NAME = "Alice"
SENSITIVE_SECRET_NAME = "SecretName"
HIDDEN_PARAMS_MARKER = "[SQL parameters hidden due to hide_parameters=True]"


def test_build_engine_enables_pool_pre_ping_for_non_sqlite(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr("shared.db.create_engine", fake_create_engine)
    _build_engine("postgresql+psycopg2://user:pass@localhost:5432/testdb")

    kwargs = captured["kwargs"]
    assert kwargs["pool_pre_ping"] is True


def test_build_engine_keeps_sqlite_pre_ping_disabled(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr("shared.db.create_engine", fake_create_engine)
    _build_engine("sqlite:///:memory:")

    kwargs = captured["kwargs"]
    assert "pool_pre_ping" not in kwargs


def test_db_error_rendering_hides_bound_parameters():
    # Build a real engine through the project's helper so the test locks the
    # configured behavior of _build_engine, not an ad-hoc engine.
    engine = _build_engine("sqlite:///:memory:")
    assert engine.hide_parameters is True

    # Deterministic failure (no such table) that still carries PII-like bound
    # parameters, so the rendered exception would expose them if hiding were off.
    statement = text(
        "INSERT INTO missing_table (email, first_name, secret_name) "
        "VALUES (:email, :first_name, :secret_name)"
    )
    params = {
        "email": SENSITIVE_EMAIL,
        "first_name": SENSITIVE_FIRST_NAME,
        "secret_name": SENSITIVE_SECRET_NAME,
    }

    try:
        with engine.connect() as connection, pytest.raises(SQLAlchemyError) as exc_info:
            connection.execute(statement, params)
    finally:
        engine.dispose()

    rendered = str(exc_info.value)
    assert SENSITIVE_EMAIL not in rendered
    assert SENSITIVE_FIRST_NAME not in rendered
    assert SENSITIVE_SECRET_NAME not in rendered
    assert HIDDEN_PARAMS_MARKER in rendered
