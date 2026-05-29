from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from fastapi_app.app.routers import router
from shared.db import init_database
from shared.logging import (
    clear_request_id,
    configure_logging,
    resolve_request_id,
    set_request_id,
)

logger = logging.getLogger(__name__)
_FORM_POST_PATHS = {"/sync-form", "/async-form"}


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
    configure_logging()

    app = FastAPI(title="Whitefly FastAPI App")

    db_url = database_url or os.getenv("DATABASE_URL", _default_database_url())
    prefix = (
        url_prefix if url_prefix is not None else os.getenv("FASTAPI_URL_PREFIX", "")
    ).rstrip("/")
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

    _register_request_observability(app)
    _register_error_observability(app)
    app.include_router(router, prefix=prefix)
    return app


def _register_request_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = resolve_request_id(request.headers.get("x-request-id"))
        set_request_id(request_id)
        request.state.request_id = request_id
        started_at = time.perf_counter()
        is_form_post = _is_form_post_request(request)

        if is_form_post:
            logger.info(
                "form post start",
                extra={
                    "event": "form_post_start",
                    "method": request.method,
                    "path": request.url.path,
                },
            )

        try:
            response = await call_next(request)
        except Exception:
            if is_form_post:
                logger.exception(
                    "form post failed",
                    extra={
                        "event": "form_post_failed",
                        "method": request.method,
                        "path": request.url.path,
                        "duration_ms": round(
                            (time.perf_counter() - started_at) * 1000, 2
                        ),
                    },
                )
            raise
        else:
            response.headers["X-Request-ID"] = request_id
            if is_form_post:
                logger.info(
                    "form post end",
                    extra={
                        "event": "form_post_end",
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": response.status_code,
                        "duration_ms": round(
                            (time.perf_counter() - started_at) * 1000, 2
                        ),
                    },
                )
            return response
        finally:
            clear_request_id()


def _register_error_observability(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, _exception: Exception
    ) -> PlainTextResponse:
        request_id = getattr(request.state, "request_id", None) or resolve_request_id(
            None
        )
        return PlainTextResponse(
            "Internal Server Error",
            status_code=500,
            headers={"X-Request-ID": request_id},
        )


def _is_form_post_request(request: Request) -> bool:
    path = request.url.path.rstrip("/") or "/"
    return request.method == "POST" and any(
        path.endswith(form_path) for form_path in _FORM_POST_PATHS
    )


app = create_app()
