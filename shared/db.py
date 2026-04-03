from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Base declarative class shared by all ORM models."""


_engine: Engine | None = None
SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False)


def _build_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def init_database(database_url: str) -> None:
    """Configure engine/session factory and create all known tables."""
    global _engine

    _engine = _build_engine(database_url)
    SessionLocal.configure(bind=_engine)

    # Import models so metadata is fully populated before create_all().
    from shared import models  # noqa: F401

    Base.metadata.create_all(bind=_engine)


def get_session() -> Session:
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()
