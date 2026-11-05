"""Optionally run the database-backed tests against PostgreSQL.

By default every test builds its own SQLite file. When ``TEST_DATABASE_URL``
points at a PostgreSQL database, every engine that ``shared.db.init_database``
builds is redirected there instead, and all application tables are dropped
first, so each test still starts from an empty schema:

    TEST_DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/whitefly_test \
        python -m pytest -q

That covers the apps, the worker and the service layer, because they all go
through ``init_database``. Two modules are left alone: ``tests/shared/test_db.py``
unit-tests ``_build_engine`` itself, and ``tests/test_migrations.py`` builds its
own SQLite engines to compare Alembic with ``create_all``.
"""

from __future__ import annotations

import os

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
_KEEP_OWN_ENGINE = {"tests.shared.test_db", "tests.test_migrations"}


def pytest_report_header(config):
    if TEST_DATABASE_URL:
        dialect = TEST_DATABASE_URL.split(":", 1)[0]
        return f"database: {dialect} via TEST_DATABASE_URL"
    return "database: per-test SQLite files"


@pytest.fixture(autouse=True)
def _redirect_engines_to_test_database(request, monkeypatch):
    if not TEST_DATABASE_URL or request.module.__name__ in _KEEP_OWN_ENGINE:
        yield
        return

    from shared import (
        db,
        models,  # noqa: F401  (populate metadata before drop_all)
    )

    real_build_engine = db._build_engine

    def build_test_engine(_requested_url: str):
        if db._engine is not None:
            db._engine.dispose()
        engine = real_build_engine(TEST_DATABASE_URL)
        db.Base.metadata.drop_all(bind=engine)
        return engine

    monkeypatch.setattr(db, "_build_engine", build_test_engine)
    yield
    if db._engine is not None:
        db._engine.dispose()
