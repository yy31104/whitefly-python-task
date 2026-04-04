from __future__ import annotations

import os

from flask_app.app.config import Config
from shared.db import init_database
from shared.services import save_submission_from_worker
from worker.celery_app import celery_app

_db_ready = False


def _ensure_database_ready() -> None:
    global _db_ready
    if _db_ready:
        return

    database_url = os.getenv("DATABASE_URL", Config.DATABASE_URL)
    init_database(database_url)
    _db_ready = True


@celery_app.task(name="worker.save_async_submission")
def save_async_submission_task(first_name: str, last_name: str, email: str) -> int:
    _ensure_database_ready()
    submission = save_submission_from_worker(
        first_name=first_name,
        last_name=last_name,
        email=email,
        form_type="async",
        status="processed",
    )
    return submission.id
