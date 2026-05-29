from __future__ import annotations

import logging
import uuid
from pathlib import Path

import worker.tasks as worker_tasks
from shared.db import session_scope
from shared.models import Submission


def test_save_async_submission_task_persists_record(
    monkeypatch, tmp_path: Path, caplog
):
    db_path = tmp_path / "worker_task.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)
    caplog.set_level(logging.INFO)

    submission_id = worker_tasks.save_async_submission_task.run(
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.com",
        request_id="worker-request-id",
    )

    with session_scope() as session:
        row = session.query(Submission).filter_by(id=submission_id).one()

    assert row.first_name == "Grace"
    assert row.last_name == "Hopper"
    assert row.email == "grace@example.com"
    assert row.form_type == "async"
    assert row.status == "processed"

    records = [record for record in caplog.records if record.name == "worker.tasks"]
    assert [record.event for record in records] == [
        "async_submission_task_start",
        "async_submission_task_success",
    ]
    assert all(record.request_id == "worker-request-id" for record in records)

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

    submission_id = worker_tasks.save_async_submission_task.run(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
    )

    with session_scope() as session:
        row = session.query(Submission).filter_by(id=submission_id).one()

    assert row.email == "ada@example.com"
    records = [record for record in caplog.records if record.name == "worker.tasks"]
    assert [record.event for record in records] == [
        "async_submission_task_start",
        "async_submission_task_success",
    ]
    assert all(uuid.UUID(record.request_id) for record in records)


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

    records = [record for record in caplog.records if record.name == "worker.tasks"]
    assert records
    assert all(record.request_id != malicious_request_id for record in records)
    assert all(uuid.UUID(record.request_id) for record in records)

    rendered_logs = "\n".join(str(record.__dict__) for record in records)
    assert malicious_request_id not in rendered_logs
    assert "katherine@example.com" not in rendered_logs
    assert "Katherine" not in rendered_logs
    assert "Johnson" not in rendered_logs
