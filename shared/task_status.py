from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from shared.db import session_scope
from shared.models import TaskStatus

logger = logging.getLogger(__name__)
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def sanitize_task_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if not _TASK_ID_PATTERN.fullmatch(value):
        return None
    return value


def mark_task_queued(task_id: str) -> None:
    try:
        with session_scope() as session:
            existing = _find_task_status(session, task_id)
            if existing is not None:
                return

            session.add(
                TaskStatus(
                    task_id=task_id,
                    status="queued",
                    retries=0,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
    except Exception as exc:  # noqa: BLE001 - best-effort status writes
        _log_status_write_failed(task_id, "queued", exc)


def record_task_status(
    task_id: str,
    status: str,
    *,
    submission_id: int | None = None,
    error_type: str | None = None,
    retries: int | None = None,
) -> None:
    try:
        with session_scope() as session:
            task_status = _find_task_status(session, task_id)
            if task_status is None:
                task_status = TaskStatus(
                    task_id=task_id,
                    status=status,
                    submission_id=submission_id,
                    error_type=error_type,
                    retries=retries or 0,
                    updated_at=_utc_now(),
                )
                session.add(task_status)
            else:
                _update_task_status(
                    task_status,
                    status,
                    submission_id=submission_id,
                    error_type=error_type,
                    retries=retries,
                )

            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = _find_task_status(session, task_id)
                if existing is None:
                    raise
                _update_task_status(
                    existing,
                    status,
                    submission_id=submission_id,
                    error_type=error_type,
                    retries=retries,
                )
                session.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort status writes
        _log_status_write_failed(task_id, status, exc)


def get_task_status(task_id: str) -> TaskStatus | None:
    with session_scope() as session:
        return _find_task_status(session, task_id)


def serialize_task_status(task_status: TaskStatus) -> dict:
    return {
        "task_id": task_status.task_id,
        "status": task_status.status,
        "submission_id": task_status.submission_id,
        "error_type": task_status.error_type,
        "retries": task_status.retries,
        "created_at": task_status.created_at.isoformat(),
        "updated_at": task_status.updated_at.isoformat(),
    }


def _find_task_status(session, task_id: str) -> TaskStatus | None:
    return session.query(TaskStatus).filter(TaskStatus.task_id == task_id).one_or_none()


def _update_task_status(
    task_status: TaskStatus,
    status: str,
    *,
    submission_id: int | None,
    error_type: str | None,
    retries: int | None,
) -> None:
    task_status.status = status
    task_status.submission_id = submission_id
    task_status.error_type = error_type
    if retries is not None:
        task_status.retries = retries
    task_status.updated_at = _utc_now()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _log_status_write_failed(task_id: str, status: str, exc: Exception) -> None:
    logger.warning(
        "task status write failed",
        extra={
            "event": "task_status_write_failed",
            "task_id": task_id,
            "status": status,
            "error_type": type(exc).__name__,
        },
    )
