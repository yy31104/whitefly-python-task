from __future__ import annotations

import logging
import uuid
from pathlib import Path

import worker.tasks as worker_tasks
from shared.db import session_scope
from shared.logging import get_request_id
from shared.models import Submission


def _worker_records(caplog):
    return [record for record in caplog.records if record.name == "worker.tasks"]


def test_save_async_submission_task_persists_record(
    monkeypatch, tmp_path: Path, caplog
):
    db_path = tmp_path / "worker_task.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
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

    records = _worker_records(caplog)
    assert [record.event for record in records] == [
        "async_submission_task_start",
        "async_submission_task_success",
    ]
    assert all(record.request_id == "worker-request-id" for record in records)
    assert all(record.task_id == "worker-success-task-id" for record in records)
    assert all(record.task_name == "worker.save_async_submission" for record in records)
    assert all(record.retries == 0 for record in records)

    rendered_logs = "\n".join(str(record.__dict__) for record in records)
    assert "grace@example.com" not in rendered_logs
    assert "Grace" not in rendered_logs
    assert "Hopper" not in rendered_logs


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
    records = _worker_records(caplog)
    assert [record.event for record in records] == [
        "async_submission_task_start",
        "async_submission_task_success",
    ]
    assert all(uuid.UUID(record.request_id) for record in records)
    assert all(record.task_id == "worker-old-message-task-id" for record in records)
    assert all(record.task_name == "worker.save_async_submission" for record in records)


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
    monkeypatch, caplog
):
    monkeypatch.setattr(worker_tasks, "_ensure_database_ready", lambda: None)

    def fake_save_submission_from_worker(**_kwargs):
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
    assert get_request_id() is None

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

    rendered_logs = "\n".join(str(record.__dict__) for record in records)
    assert "failure@example.com" not in rendered_logs
    assert "FailureFirst" not in rendered_logs
    assert "FailureLast" not in rendered_logs
    assert "SECRET_KEY" not in rendered_logs
    assert "DATABASE_URL" not in rendered_logs
    assert "REDIS_URL" not in rendered_logs
    assert "CELERY_BROKER_URL" not in rendered_logs
    assert "first_name" not in rendered_logs
    assert "last_name" not in rendered_logs
    assert "email" not in rendered_logs
