from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from fastapi_app.app.routers import router
from shared.db import init_database


def _default_database_url() -> str:
    root_dir = Path(__file__).resolve().parents[2]
    return f"sqlite:///{(root_dir / 'app.db').as_posix()}"


def create_app(
    database_url: str | None = None,
    url_prefix: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Whitefly FastAPI App")

    db_url = database_url or os.getenv("DATABASE_URL", _default_database_url())
    prefix = (url_prefix if url_prefix is not None else os.getenv("FASTAPI_URL_PREFIX", "")).rstrip("/")
    init_database(db_url)
    app.state.database_url = db_url
    app.state.url_prefix = prefix

    app.include_router(router, prefix=prefix)
    return app


app = create_app()
