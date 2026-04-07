from __future__ import annotations

from types import SimpleNamespace

from shared.db import _build_engine


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
