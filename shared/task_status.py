from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from shared.db import session_scope
from shared.models import TaskStatus

logger = logging.getLogger(__name__)
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

# A task that reached one of these states never leaves it. With acks_late a
# message can be delivered again after the task finished; the duplicate run
# must not move a processed task back to "processing" or "retrying".
TERMINAL_TASK_STATUSES = frozenset({"processed", "failed"})


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
    """Move a task to ``status`` unless it already reached a terminal state.

    The transition is one conditional UPDATE, so the "is it terminal?" check and
    the write cannot be split by a concurrent writer. Only when no row exists is
    a new row inserted; a racing insert falls back to the same conditional UPDATE.
    """
    try:
        with session_scope() as session:
            if _update_unless_terminal(
                session,
                task_id,
                status,
                submission_id=submission_id,
                error_type=error_type,
                retries=retries,
            ):
                session.commit()
                return
            # End the no-op UPDATE's transaction before inserting, so SQLite does
            # not keep a write lock that a concurrent inserter would wait on.
            session.rollback()

            if _find_task_status(session, task_id) is not None:
                # The row exists and is terminal: leave it as it is.
                return

            session.add(
                TaskStatus(
                    task_id=task_id,
                    status=status,
                    submission_id=submission_id,
                    error_type=error_type,
                    retries=retries or 0,
                    updated_at=_utc_now(),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                _update_unless_terminal(
                    session,
                    task_id,
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


def _update_unless_terminal(
    session,
    task_id: str,
    status: str,
    *,
    submission_id: int | None,
    error_type: str | None,
    retries: int | None,
) -> bool:
    values = {
        "status": status,
        "submission_id": submission_id,
        "error_type": error_type,
        "updated_at": _utc_now(),
    }
    if retries is not None:
        values["retries"] = retries
    result = session.execute(
        update(TaskStatus)
        .where(
            TaskStatus.task_id == task_id,
            TaskStatus.status.not_in(TERMINAL_TASK_STATUSES),
        )
        .values(**values)
    )
    return result.rowcount > 0


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
