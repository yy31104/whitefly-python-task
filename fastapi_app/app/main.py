from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from fastapi_app.app.routers import router
from shared.db import init_database


def _default_database_url() -> str:
    root_dir = Path(__file__).resolve().parents[2]
    return f"sqlite:///{(root_dir / 'app.db').as_posix()}"


def _read_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _read_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_app(
    database_url: str | None = None,
    url_prefix: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Whitefly FastAPI App")

    db_url = database_url or os.getenv("DATABASE_URL", _default_database_url())
    prefix = (url_prefix if url_prefix is not None else os.getenv("FASTAPI_URL_PREFIX", "")).rstrip("/")
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    secret_key = os.getenv("SECRET_KEY")
    if app_env == "production" and not secret_key:
        raise RuntimeError("SECRET_KEY must be explicitly set when APP_ENV=production.")

    init_database(db_url)
    app.state.database_url = db_url
    app.state.url_prefix = prefix
    app.state.app_env = app_env
    app.state.enable_submissions_page = _read_bool_env(
        "ENABLE_SUBMISSIONS_PAGE",
        default=app_env != "production",
    )
    app.state.redis_url = os.getenv("REDIS_URL", "")
    app.state.submissions_page_limit = _read_int_env("SUBMISSIONS_PAGE_LIMIT", 200)
    app.state.rate_limit_post_requests = _read_int_env("RATE_LIMIT_POST_REQUESTS", 20)
    app.state.rate_limit_window_seconds = _read_int_env("RATE_LIMIT_WINDOW_SECONDS", 60)

    app.include_router(router, prefix=prefix)
    return app


app = create_app()
