from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy.exc import OperationalError

import worker.tasks as worker_tasks
from shared.db import session_scope
from shared.logging import get_request_id
from shared.models import Submission, TaskStatus
from shared.services import IdempotencyConflict


def _worker_records(caplog):
    return [record for record in caplog.records if record.name == "worker.tasks"]


def _assert_logs_are_pii_safe(records, *values: str) -> None:
    rendered_logs = "\n".join(str(record.__dict__) for record in records)
    for value in values:
        assert value not in rendered_logs
    assert "SECRET_KEY" not in rendered_logs
    assert "DATABASE_URL" not in rendered_logs
    assert "REDIS_URL" not in rendered_logs
    assert "CELERY_BROKER_URL" not in rendered_logs
    assert "first_name" not in rendered_logs
    assert "last_name" not in rendered_logs
    assert "email" not in rendered_logs


def _get_task_status(task_id: str) -> TaskStatus:
    with session_scope() as session:
        return session.query(TaskStatus).filter_by(task_id=task_id).one()


def _count_task_statuses() -> int:
    with session_scope() as session:
        return session.query(TaskStatus).count()


def _assert_task_statuses_are_pii_safe(*values: str) -> None:
    with session_scope() as session:
        rendered_rows = "\n".join(
            str(
                {
                    "task_id": row.task_id,
                    "status": row.status,
                    "submission_id": row.submission_id,
                    "error_type": row.error_type,
                    "retries": row.retries,
                }
            )
            for row in session.query(TaskStatus).all()
        )
    for value in values:
        assert value not in rendered_rows
    assert "SECRET_KEY" not in rendered_rows
    assert "DATABASE_URL" not in rendered_rows
    assert "REDIS_URL" not in rendered_rows
    assert "CELERY_BROKER_URL" not in rendered_rows


def test_save_async_submission_task_retry_config():
    assert OperationalError in worker_tasks.save_async_submission_task.autoretry_for
    assert worker_tasks.save_async_submission_task.max_retries == 3
    assert worker_tasks.save_async_submission_task.acks_late is True
    assert worker_tasks.save_async_submission_task.reject_on_worker_lost is True


def test_save_async_submission_task_persists_record(
    monkeypatch, tmp_path: Path, caplog
):
    db_path = tmp_path / "worker_task.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
    status_calls: list[dict[str, object]] = []
    real_record_task_status = worker_tasks.record_task_status

    def spy_record_task_status(task_id, status, **kwargs):
        status_calls.append({"task_id": task_id, "status": status, **kwargs})
        return real_record_task_status(task_id, status, **kwargs)

    monkeypatch.setattr(worker_tasks, "record_task_status", spy_record_task_status)
    caplog.set_level(logging.INFO)

    result = worker_tasks.save_async_submission_task.apply(
        kwargs={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "request_id": "worker-request-id",
        },
        task_id="worker-success-task-id",
        throw=True,
    )
    submission_id = result.result

    with session_scope() as session:
        row = session.query(Submission).filter_by(id=submission_id).one()

    assert row.first_name == "Grace"
    assert row.last_name == "Hopper"
    assert row.email == "grace@example.com"
    assert row.form_type == "async"
    assert row.status == "processed"
    assert row.idempotency_key == "worker-success-task-id"
    task_status = _get_task_status("worker-success-task-id")
    assert task_status.status == "processed"
    assert task_status.submission_id == submission_id
    assert task_status.error_type is None
    assert task_status.retries == 0
    assert status_calls == [
        {
            "task_id": "worker-success-task-id",
            "status": "processing",
            "submission_id": None,
            "error_type": None,
            "retries": 0,
        },
        {
            "task_id": "worker-success-task-id",
            "status": "processed",
            "submission_id": submission_id,
            "error_type": None,
            "retries": 0,
        },
    ]

    records = _worker_records(caplog)
    assert [record.event for record in records] == [
        "async_submission_task_start",
        "async_submission_task_success",
    ]
    assert all(record.request_id == "worker-request-id" for record in records)
    assert all(record.task_id == "worker-success-task-id" for record in records)
    assert all(record.task_name == "worker.save_async_submission" for record in records)
    assert all(record.retries == 0 for record in records)
    assert all(
        record.effective_idempotency_key == "worker-success-task-id"
        for record in records
    )

    _assert_logs_are_pii_safe(records, "grace@example.com", "Grace", "Hopper")
    _assert_task_statuses_are_pii_safe("grace@example.com", "Grace", "Hopper")


def test_save_async_submission_task_accepts_missing_request_id(
    monkeypatch, tmp_path: Path, caplog
):
    db_path = tmp_path / "worker_task_old_message.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
    caplog.set_level(logging.INFO)

    result = worker_tasks.save_async_submission_task.apply(
        kwargs={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
        },
        task_id="worker-old-message-task-id",
        throw=True,
    )
    submission_id = result.result

    with session_scope() as session:
        row = session.query(Submission).filter_by(id=submission_id).one()

    assert row.email == "ada@example.com"
    assert row.idempotency_key == "worker-old-message-task-id"
    records = _worker_records(caplog)
    assert [record.event for record in records] == [
        "async_submission_task_start",
        "async_submission_task_success",
    ]
    assert all(uuid.UUID(record.request_id) for record in records)
    assert all(record.task_id == "worker-old-message-task-id" for record in records)
    assert all(record.task_name == "worker.save_async_submission" for record in records)
    assert all(
        record.effective_idempotency_key == "worker-old-message-task-id"
        for record in records
    )


def test_save_async_submission_task_persists_idempotency_key(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "worker_task_idempotency_key.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)

    result = worker_tasks.save_async_submission_task.apply(
        kwargs={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "worker-idempotency@example.com",
            "idempotency_key": "worker-idempotency-key",
        },
        task_id="worker-idempotency-task-id",
        throw=True,
    )

    with session_scope() as session:
        row = session.query(Submission).filter_by(id=result.result).one()

    assert row.idempotency_key == "worker-idempotency-key"
    assert row.form_type == "async"


def test_save_async_submission_task_reuses_same_idempotency_key(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "worker_task_idempotency_reuse.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
    kwargs = {
        "first_name": "Grace",
        "last_name": "Hopper",
        "email": "worker-idempotency-reuse@example.com",
        "idempotency_key": "worker-idempotency-reuse-key",
    }

    first = worker_tasks.save_async_submission_task.apply(
        kwargs=kwargs,
        task_id="worker-idempotency-reuse-task-1",
        throw=True,
    )
    second = worker_tasks.save_async_submission_task.apply(
        kwargs=kwargs,
        task_id="worker-idempotency-reuse-task-2",
        throw=True,
    )

    with session_scope() as session:
        count = session.query(Submission).count()

    assert second.result == first.result
    assert count == 1


def test_save_async_submission_task_retries_transient_operational_error_once(
    monkeypatch, tmp_path: Path, caplog
):
    db_path = tmp_path / "worker_task_transient_retry.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
    real_save_submission_from_worker = worker_tasks.save_submission_from_worker
    real_record_task_status = worker_tasks.record_task_status
    attempts: list[dict[str, str]] = []
    status_calls: list[dict[str, object]] = []

    def flaky_save_submission_from_worker(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise OperationalError(
                "insert into submissions",
                {},
                Exception("temporary database outage"),
            )
        return real_save_submission_from_worker(**kwargs)

    monkeypatch.setattr(
        worker_tasks,
        "save_submission_from_worker",
        flaky_save_submission_from_worker,
    )

    def spy_record_task_status(task_id, status, **kwargs):
        status_calls.append({"task_id": task_id, "status": status, **kwargs})
        return real_record_task_status(task_id, status, **kwargs)

    monkeypatch.setattr(worker_tasks, "record_task_status", spy_record_task_status)
    caplog.set_level(logging.INFO)

    result = worker_tasks.save_async_submission_task.apply(
        kwargs={
            "first_name": "RetryFirst",
            "last_name": "RetryLast",
            "email": "retry@example.com",
            "request_id": "worker-retry-request-id",
        },
        task_id="worker-transient-retry-task-id",
        throw=False,
    )

    with session_scope() as session:
        count = session.query(Submission).count()
        row = session.query(Submission).filter_by(id=result.result).one()

    assert result.successful()
    assert len(attempts) == 2
    assert count == 1
    assert row.idempotency_key == "worker-transient-retry-task-id"
    task_status = _get_task_status("worker-transient-retry-task-id")
    assert task_status.status == "processed"
    assert task_status.submission_id == result.result
    assert task_status.error_type is None
    assert task_status.retries == 1
    assert all(
        attempt["idempotency_key"] == "worker-transient-retry-task-id"
        for attempt in attempts
    )
    assert [call["status"] for call in status_calls] == [
        "processing",
        "retrying",
        "processing",
        "processed",
    ]

    records = _worker_records(caplog)
    assert [record.event for record in records] == [
        "async_submission_task_start",
        "async_submission_task_retry",
        "async_submission_task_start",
        "async_submission_task_success",
    ]
    assert records[1].error_type == "OperationalError"
    assert [record.retries for record in records] == [0, 0, 1, 1]
    assert all(
        record.effective_idempotency_key == "worker-transient-retry-task-id"
        for record in records
    )
    _assert_logs_are_pii_safe(
        records,
        "RetryFirst",
        "RetryLast",
        "retry@example.com",
    )


def test_exhausted_operational_error_logs_terminal_failure(
    monkeypatch, tmp_path: Path, caplog
):
    db_path = tmp_path / "worker_task_exhausted_operational_error.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
    worker_tasks._ensure_database_ready()
    monkeypatch.setattr(worker_tasks, "_ensure_database_ready", lambda: None)
    attempts: list[None] = []

    def fake_save_submission_from_worker(**_kwargs):
        attempts.append(None)
        raise OperationalError(
            "insert into submissions",
            {},
            Exception("temporary database outage"),
        )

    monkeypatch.setattr(
        worker_tasks,
        "save_submission_from_worker",
        fake_save_submission_from_worker,
    )
    caplog.set_level(logging.INFO)

    result = worker_tasks.save_async_submission_task.apply(
        kwargs={
            "first_name": "ExhaustedFirst",
            "last_name": "ExhaustedLast",
            "email": "exhausted@example.com",
        },
        task_id="worker-exhausted-task-id",
        retries=3,
        throw=False,
    )

    assert result.failed()
    assert isinstance(result.result, OperationalError)
    assert len(attempts) == 1
    assert get_request_id() is None
    task_status = _get_task_status("worker-exhausted-task-id")
    assert task_status.status == "failed"
    assert task_status.submission_id is None
    assert task_status.error_type == "OperationalError"
    assert task_status.retries == 3

    records = _worker_records(caplog)
    assert [record.event for record in records] == [
        "async_submission_task_start",
        "async_submission_task_failed",
    ]
    failure_record = records[-1]
    assert failure_record.error_type == "OperationalError"
    assert failure_record.retries == 3
    assert failure_record.exc_info is not None
    assert failure_record.effective_idempotency_key == "worker-exhausted-task-id"
    _assert_logs_are_pii_safe(
        records,
        "ExhaustedFirst",
        "ExhaustedLast",
        "exhausted@example.com",
    )


def test_redelivered_keyless_task_reuses_same_task_id_without_duplicate(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "worker_task_redelivery_no_duplicate.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
    kwargs = {
        "first_name": "Grace",
        "last_name": "Hopper",
        "email": "worker-redelivery-dedup@example.com",
    }

    first = worker_tasks.save_async_submission_task.apply(
        kwargs=kwargs,
        task_id="worker-redelivery-dedup-task-id",
        throw=True,
    )

    with session_scope() as session:
        assert session.query(Submission).count() == 1

    second = worker_tasks.save_async_submission_task.apply(
        kwargs=kwargs,
        task_id="worker-redelivery-dedup-task-id",
        throw=True,
    )

    with session_scope() as session:
        count = session.query(Submission).count()
        row = session.query(Submission).filter_by(id=first.result).one()

    task_status = _get_task_status("worker-redelivery-dedup-task-id")
    assert second.result == first.result
    assert count == 1
    assert _count_task_statuses() == 1
    assert row.idempotency_key == "worker-redelivery-dedup-task-id"
    assert task_status.status == "processed"
    assert task_status.submission_id == first.result
    assert task_status.error_type is None


def test_keyless_async_submission_task_uses_task_id_as_idempotency_key(
    monkeypatch, tmp_path: Path
):
    db_path = tmp_path / "worker_task_task_id_key.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)

    result = worker_tasks.save_async_submission_task.apply(
        kwargs={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "task-id-key@example.com",
        },
        task_id="task-id-123",
        throw=True,
    )

    with session_scope() as session:
        row = session.query(Submission).filter_by(id=result.result).one()

    assert row.idempotency_key == "task-id-123"


def test_record_task_status_failure_does_not_break_task_success(
    monkeypatch, tmp_path: Path, caplog
):
    db_path = tmp_path / "worker_task_status_write_failure.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)

    def fail_record_task_status(*_args, **_kwargs):
        raise RuntimeError("status write failed")

    monkeypatch.setattr(worker_tasks, "record_task_status", fail_record_task_status)
    caplog.set_level(logging.INFO)

    result = worker_tasks.save_async_submission_task.apply(
        kwargs={
            "first_name": "StatusFirst",
            "last_name": "StatusLast",
            "email": "status-write-failure@example.com",
        },
        task_id="worker-status-write-failure-task-id",
        throw=True,
    )

    with session_scope() as session:
        row = session.query(Submission).filter_by(id=result.result).one()

    assert row.idempotency_key == "worker-status-write-failure-task-id"
    assert _count_task_statuses() == 0
    records = _worker_records(caplog)
    status_write_failures = [
        record for record in records if record.event == "task_status_write_failed"
    ]
    assert [record.status for record in status_write_failures] == [
        "processing",
        "processed",
    ]
    _assert_logs_are_pii_safe(
        records,
        "StatusFirst",
        "StatusLast",
        "status-write-failure@example.com",
    )


def test_non_retryable_idempotency_conflict_is_terminal(
    monkeypatch, tmp_path: Path, caplog
):
    db_path = tmp_path / "worker_task_idempotency_conflict.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
    worker_tasks._ensure_database_ready()
    monkeypatch.setattr(worker_tasks, "_ensure_database_ready", lambda: None)
    attempts: list[None] = []

    def fake_save_submission_from_worker(**_kwargs):
        attempts.append(None)
        raise IdempotencyConflict("conflicting idempotency key")

    monkeypatch.setattr(
        worker_tasks,
        "save_submission_from_worker",
        fake_save_submission_from_worker,
    )
    caplog.set_level(logging.INFO)

    result = worker_tasks.save_async_submission_task.apply(
        kwargs={
            "first_name": "ConflictFirst",
            "last_name": "ConflictLast",
            "email": "conflict@example.com",
            "idempotency_key": "conflict-key",
        },
        task_id="worker-conflict-task-id",
        throw=False,
    )

    assert result.failed()
    assert isinstance(result.result, IdempotencyConflict)
    assert len(attempts) == 1
    assert get_request_id() is None
    task_status = _get_task_status("worker-conflict-task-id")
    assert task_status.status == "conflict"
    assert task_status.submission_id is None
    assert task_status.error_type == "IdempotencyConflict"
    assert task_status.retries == 0

    records = _worker_records(caplog)
    assert [record.event for record in records] == [
        "async_submission_task_start",
        "async_submission_task_failed",
    ]
    failure_record = records[-1]
    assert failure_record.error_type == "IdempotencyConflict"
    assert failure_record.exc_info is not None
    assert failure_record.effective_idempotency_key == "conflict-key"
    _assert_logs_are_pii_safe(
        records,
        "ConflictFirst",
        "ConflictLast",
        "conflict@example.com",
    )


def test_save_async_submission_task_replaces_unsafe_request_id(
    monkeypatch, tmp_path: Path, caplog
):
    db_path = tmp_path / "worker_task_unsafe_request_id.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
    caplog.set_level(logging.INFO)
    malicious_request_id = "evil\nrequest"

    worker_tasks.save_async_submission_task.run(
        first_name="Katherine",
        last_name="Johnson",
        email="katherine@example.com",
        request_id=malicious_request_id,
    )

    records = _worker_records(caplog)
    assert records
    assert all(record.request_id != malicious_request_id for record in records)
    assert all(uuid.UUID(record.request_id) for record in records)

    rendered_logs = "\n".join(str(record.__dict__) for record in records)
    assert malicious_request_id not in rendered_logs
    assert "katherine@example.com" not in rendered_logs
    assert "Katherine" not in rendered_logs
    assert "Johnson" not in rendered_logs


def test_save_async_submission_task_failure_logs_task_context_and_clears_request_id(
    monkeypatch, tmp_path: Path, caplog
):
    db_path = tmp_path / "worker_task_runtime_failure.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
    worker_tasks._ensure_database_ready()
    monkeypatch.setattr(worker_tasks, "_ensure_database_ready", lambda: None)
    attempts: list[None] = []

    def fake_save_submission_from_worker(**_kwargs):
        attempts.append(None)
        raise RuntimeError("database write failed")

    monkeypatch.setattr(
        worker_tasks, "save_submission_from_worker", fake_save_submission_from_worker
    )
    caplog.set_level(logging.INFO)

    result = worker_tasks.save_async_submission_task.apply(
        kwargs={
            "first_name": "FailureFirst",
            "last_name": "FailureLast",
            "email": "failure@example.com",
            "request_id": "worker-failure-request-id",
        },
        task_id="worker-failure-task-id",
        retries=2,
        throw=False,
    )

    assert result.failed()
    assert isinstance(result.result, RuntimeError)
    assert len(attempts) == 1
    assert get_request_id() is None
    task_status = _get_task_status("worker-failure-task-id")
    assert task_status.status == "failed"
    assert task_status.submission_id is None
    assert task_status.error_type == "RuntimeError"
    assert task_status.retries == 2

    records = _worker_records(caplog)
    assert [record.event for record in records] == [
        "async_submission_task_start",
        "async_submission_task_failed",
    ]

    failure_record = records[-1]
    assert failure_record.request_id == "worker-failure-request-id"
    assert failure_record.task_id == "worker-failure-task-id"
    assert failure_record.task_name == "worker.save_async_submission"
    assert failure_record.retries == 2
    assert failure_record.error_type == "RuntimeError"
    assert failure_record.exc_info is not None

    _assert_logs_are_pii_safe(
        records,
        "failure@example.com",
        "FailureFirst",
        "FailureLast",
    )
