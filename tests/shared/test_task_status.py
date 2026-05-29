from __future__ import annotations

import logging

from sqlalchemy.orm import Session as SQLAlchemySession

import shared.task_status as task_status_module
from shared.db import init_database, session_scope
from shared.models import TaskStatus
from shared.task_status import mark_task_queued, record_task_status


def _get_task_status(task_id: str) -> TaskStatus:
    with session_scope() as session:
        return session.query(TaskStatus).filter_by(task_id=task_id).one()


def _count_task_statuses() -> int:
    with session_scope() as session:
        return session.query(TaskStatus).count()


def _task_status_records(caplog):
    return [record for record in caplog.records if record.name == "shared.task_status"]


def _assert_no_pii_logged(caplog) -> None:
    rendered_logs = "\n".join(
        str(record.__dict__) for record in _task_status_records(caplog)
    )
    assert "Ada" not in rendered_logs
    assert "Lovelace" not in rendered_logs
    assert "ada@example.com" not in rendered_logs
    assert "SECRET_KEY" not in rendered_logs
    assert "DATABASE_URL" not in rendered_logs
    assert "REDIS_URL" not in rendered_logs
    assert "CELERY_BROKER_URL" not in rendered_logs


def test_mark_task_queued_inserts_queued(tmp_path):
    init_database(f"sqlite:///{(tmp_path / 'task_status_queued.db').as_posix()}")

    mark_task_queued("task-queued")

    row = _get_task_status("task-queued")
    assert row.status == "queued"
    assert row.submission_id is None
    assert row.error_type is None
    assert row.retries == 0


def test_mark_task_queued_does_not_overwrite_existing_status(tmp_path):
    init_database(f"sqlite:///{(tmp_path / 'task_status_no_downgrade.db').as_posix()}")
    record_task_status(
        "task-existing",
        "processed",
        submission_id=123,
        retries=2,
    )

    mark_task_queued("task-existing")

    row = _get_task_status("task-existing")
    assert row.status == "processed"
    assert row.submission_id == 123
    assert row.retries == 2


def test_record_task_status_inserts_if_absent(tmp_path):
    init_database(f"sqlite:///{(tmp_path / 'task_status_insert.db').as_posix()}")

    record_task_status("task-insert", "processing", retries=1)

    row = _get_task_status("task-insert")
    assert row.status == "processing"
    assert row.retries == 1
    assert row.created_at is not None
    assert row.updated_at is not None


def test_record_task_status_updates_existing_status(tmp_path):
    init_database(f"sqlite:///{(tmp_path / 'task_status_update.db').as_posix()}")
    mark_task_queued("task-update")
    created_at = _get_task_status("task-update").created_at

    record_task_status(
        "task-update",
        "processed",
        submission_id=456,
        retries=3,
    )

    row = _get_task_status("task-update")
    assert row.status == "processed"
    assert row.submission_id == 456
    assert row.error_type is None
    assert row.retries == 3
    assert row.created_at == created_at
    assert row.updated_at is not None


def test_record_task_status_preserves_one_row_per_task_id(tmp_path):
    init_database(f"sqlite:///{(tmp_path / 'task_status_one_row.db').as_posix()}")

    record_task_status("task-one-row", "queued")
    record_task_status("task-one-row", "processing")
    record_task_status("task-one-row", "processed", submission_id=789)

    row = _get_task_status("task-one-row")
    assert row.status == "processed"
    assert row.submission_id == 789
    assert _count_task_statuses() == 1


def test_record_task_status_integrity_error_race_updates_existing(
    monkeypatch, tmp_path
):
    init_database(f"sqlite:///{(tmp_path / 'task_status_race.db').as_posix()}")
    original_commit = SQLAlchemySession.commit
    race_inserted = False

    def commit_with_race(session):
        nonlocal race_inserted
        should_insert_race = any(
            isinstance(item, TaskStatus) and item.task_id == "task-race"
            for item in session.new
        )
        if should_insert_race and not race_inserted:
            race_inserted = True
            with session_scope() as other_session:
                other_session.add(
                    TaskStatus(
                        task_id="task-race",
                        status="queued",
                        retries=0,
                    )
                )
                original_commit(other_session)
        return original_commit(session)

    monkeypatch.setattr(SQLAlchemySession, "commit", commit_with_race)

    record_task_status(
        "task-race",
        "processed",
        submission_id=321,
        retries=1,
    )

    row = _get_task_status("task-race")
    assert row.status == "processed"
    assert row.submission_id == 321
    assert row.retries == 1
    assert _count_task_statuses() == 1


def test_record_task_status_failure_is_logged_and_swallowed(
    monkeypatch, tmp_path, caplog
):
    init_database(f"sqlite:///{(tmp_path / 'task_status_failure.db').as_posix()}")

    def broken_session_scope():
        raise RuntimeError("status database unavailable")

    monkeypatch.setattr(task_status_module, "session_scope", broken_session_scope)
    caplog.set_level(logging.WARNING)

    record_task_status(
        "task-failure",
        "failed",
        error_type="RuntimeError",
        retries=2,
    )

    records = _task_status_records(caplog)
    assert [record.event for record in records] == ["task_status_write_failed"]
    assert records[0].task_id == "task-failure"
    assert records[0].status == "failed"
    assert records[0].error_type == "RuntimeError"
    _assert_no_pii_logged(caplog)


def test_mark_task_queued_failure_is_logged_and_swallowed(
    monkeypatch, tmp_path, caplog
):
    init_database(f"sqlite:///{(tmp_path / 'task_status_mark_failure.db').as_posix()}")

    def broken_session_scope():
        raise RuntimeError("status database unavailable")

    monkeypatch.setattr(task_status_module, "session_scope", broken_session_scope)
    caplog.set_level(logging.WARNING)

    mark_task_queued("task-mark-failure")

    records = _task_status_records(caplog)
    assert [record.event for record in records] == ["task_status_write_failed"]
    assert records[0].task_id == "task-mark-failure"
    assert records[0].status == "queued"
    assert records[0].error_type == "RuntimeError"
    _assert_no_pii_logged(caplog)
