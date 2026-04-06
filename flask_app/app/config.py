from __future__ import annotations

import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "app.db"


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    SECRET_KEY_IS_SET = bool(os.getenv("SECRET_KEY"))
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
    FLASK_URL_PREFIX = os.getenv("FLASK_URL_PREFIX", "")
    ENABLE_SUBMISSIONS_PAGE = _as_bool(
        os.getenv("ENABLE_SUBMISSIONS_PAGE"),
        default=APP_ENV != "production",
    )
    RATE_LIMIT_POST_REQUESTS = int(os.getenv("RATE_LIMIT_POST_REQUESTS", "20"))
    RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
    TESTING = False
