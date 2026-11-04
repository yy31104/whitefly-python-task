from __future__ import annotations

import logging
import time

from flask import Flask, g, request

from flask_app.app.config import Config
from shared.db import init_database
from shared.logging import (
    clear_request_id,
    configure_logging,
    get_request_id,
    resolve_request_id,
    set_request_id,
)

logger = logging.getLogger(__name__)
_FORM_POST_ENDPOINTS = {"main.sync_form", "main.async_form"}


def create_app(config_override: dict | None = None) -> Flask:
    configure_logging()

    app = Flask(__name__, template_folder="../templates")
    app.config.from_object(Config)

    if config_override:
        app.config.update(config_override)

    app_env = str(app.config.get("APP_ENV", "development")).strip().lower()
    secret_key = app.config.get("SECRET_KEY")
    secret_key_is_set = bool(app.config.get("SECRET_KEY_IS_SET")) or bool(
        config_override and config_override.get("SECRET_KEY")
    )
    if app_env == "production" and not secret_key_is_set:
        raise RuntimeError("SECRET_KEY must be explicitly set when APP_ENV=production.")
    if app_env == "production" and not secret_key:
        raise RuntimeError("SECRET_KEY must not be empty when APP_ENV=production.")

    init_database(app.config["DATABASE_URL"])

    from flask_app.app.routes import bp

    app.register_blueprint(bp, url_prefix=app.config.get("FLASK_URL_PREFIX", ""))
    _register_request_observability(app)
    return app


def _register_request_observability(app: Flask) -> None:
    @app.before_request
    def bind_request_id() -> None:
        request_id = resolve_request_id(request.headers.get("X-Request-ID"))
        set_request_id(request_id)
        g.request_id = request_id
        g.request_started_at = time.perf_counter()

        if _is_form_post_request():
            logger.info(
                "form post start",
                extra={
                    "event": "form_post_start",
                    "method": request.method,
                    "path": request.path,
                    "endpoint": request.endpoint,
                },
            )

    @app.after_request
    def echo_request_id(response):
        request_id = (
            getattr(g, "request_id", None)
            or get_request_id()
            or resolve_request_id(None)
        )
        response.headers["X-Request-ID"] = request_id

        if _is_form_post_request():
            started_at = getattr(g, "request_started_at", None)
            duration_ms = None
            if started_at is not None:
                duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.info(
                "form post end",
                extra={
                    "event": "form_post_end",
                    "method": request.method,
                    "path": request.path,
                    "endpoint": request.endpoint,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        return response

    @app.teardown_request
    def release_request_id(_exception: BaseException | None = None) -> None:
        clear_request_id()


def _is_form_post_request() -> bool:
    return request.method == "POST" and request.endpoint in _FORM_POST_ENDPOINTS
