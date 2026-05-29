from __future__ import annotations

import logging
import os

from flask_app.app.config import Config
from shared.db import init_database
from shared.logging import (
    clear_request_id,
    configure_logging,
    resolve_request_id,
    set_request_id,
)
from shared.services import save_submission_from_worker
from worker.celery_app import celery_app

logger = logging.getLogger(__name__)
_db_ready = False


def _ensure_database_ready() -> None:
    global _db_ready
    if _db_ready:
        return

    database_url = os.getenv("DATABASE_URL", Config.DATABASE_URL)
    init_database(database_url)
    _db_ready = True


@celery_app.task(name="worker.save_async_submission")
def save_async_submission_task(
    first_name: str,
    last_name: str,
    email: str,
    request_id: str | None = None,
) -> int:
    configure_logging()
    set_request_id(resolve_request_id(request_id))
    logger.info(
        "async submission task start", extra={"event": "async_submission_task_start"}
    )
    try:
        _ensure_database_ready()
        submission = save_submission_from_worker(
            first_name=first_name,
            last_name=last_name,
            email=email,
            form_type="async",
            status="processed",
        )
        logger.info(
            "async submission task success",
            extra={
                "event": "async_submission_task_success",
                "submission_id": submission.id,
            },
        )
        return submission.id
    except Exception:
        logger.exception(
            "async submission task failed",
            extra={"event": "async_submission_task_failed"},
        )
        raise
    finally:
        clear_request_id()
