from __future__ import annotations

import logging
import os

from sqlalchemy.exc import OperationalError

from flask_app.app.config import Config
from shared.db import init_database
from shared.idempotency import sanitize_idempotency_key
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
_TASK_NAME = "worker.save_async_submission"


def _ensure_database_ready() -> None:
    global _db_ready
    if _db_ready:
        return

    database_url = os.getenv("DATABASE_URL", Config.DATABASE_URL)
    init_database(database_url)
    _db_ready = True


@celery_app.task(
    bind=True,
    name=_TASK_NAME,
    autoretry_for=(OperationalError,),
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=30,
    retry_jitter=True,
    acks_late=True,
    reject_on_worker_lost=True,
)
def save_async_submission_task(
    self,
    first_name: str,
    last_name: str,
    email: str,
    request_id: str | None = None,
    idempotency_key: str | None = None,
) -> int:
    configure_logging()
    set_request_id(resolve_request_id(request_id))
    resolved_idempotency_key = sanitize_idempotency_key(idempotency_key)
    task_id = getattr(self.request, "id", None)
    retries = getattr(self.request, "retries", 0) or 0
    effective_idempotency_key = resolved_idempotency_key or task_id
    task_log_context = {
        "task_id": task_id,
        "task_name": _TASK_NAME,
        "retries": retries,
    }
    if effective_idempotency_key:
        task_log_context["effective_idempotency_key"] = effective_idempotency_key
    logger.info(
        "async submission task start",
        extra={
            "event": "async_submission_task_start",
            **task_log_context,
        },
    )
    try:
        _ensure_database_ready()
        submission = save_submission_from_worker(
            first_name=first_name,
            last_name=last_name,
            email=email,
            form_type="async",
            status="processed",
            idempotency_key=effective_idempotency_key,
        )
        logger.info(
            "async submission task success",
            extra={
                "event": "async_submission_task_success",
                **task_log_context,
                "submission_id": submission.id,
            },
        )
        return submission.id
    except Exception as exc:
        if _will_autoretry(self, exc, retries):
            logger.warning(
                "async submission task retry scheduled",
                extra={
                    "event": "async_submission_task_retry",
                    **task_log_context,
                    "error_type": type(exc).__name__,
                },
            )
        else:
            logger.exception(
                "async submission task failed",
                extra={
                    "event": "async_submission_task_failed",
                    **task_log_context,
                    "error_type": type(exc).__name__,
                },
            )
        raise
    finally:
        clear_request_id()


def _will_autoretry(task, exc: Exception, retries: int) -> bool:
    max_retries = getattr(task, "max_retries", None)
    return isinstance(exc, OperationalError) and (
        max_retries is None or retries < max_retries
    )
