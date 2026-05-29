from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session as SQLAlchemySession

from shared.db import init_database, session_scope
from shared.models import Submission
from shared.services import (
    IdempotencyConflict,
    create_submission_sync,
    list_submissions,
    save_submission_from_worker,
)


@pytest.fixture
def service_db(tmp_path):
    db_path = tmp_path / "services_test.db"
    init_database(f"sqlite:///{db_path.as_posix()}")


def _count_submissions() -> int:
    with session_scope() as session:
        return session.query(Submission).count()


def _get_submission(submission_id: int) -> Submission:
    with session_scope() as session:
        return session.query(Submission).filter_by(id=submission_id).one()


def _service_records(caplog):
    return [record for record in caplog.records if record.name == "shared.services"]


def _rendered_service_logs(caplog) -> str:
    return "\n".join(str(record.__dict__) for record in _service_records(caplog))


def _assert_no_pii_logged(caplog) -> None:
    rendered_logs = _rendered_service_logs(caplog)
    assert "Ada" not in rendered_logs
    assert "Lovelace" not in rendered_logs
    assert "Grace" not in rendered_logs
    assert "Hopper" not in rendered_logs
    assert "Katherine" not in rendered_logs
    assert "Johnson" not in rendered_logs
    assert "reuse@example.com" not in rendered_logs
    assert "conflict@example.com" not in rendered_logs
    assert "race@example.com" not in rendered_logs
    assert "SECRET_KEY" not in rendered_logs


def test_none_idempotency_key_inserts_twice(service_db):
    first = create_submission_sync(
        first_name="Ada",
        last_name="Lovelace",
        email="none-key@example.com",
    )
    second = create_submission_sync(
        first_name="Ada",
        last_name="Lovelace",
        email="none-key@example.com",
    )

    assert first.id != second.id
    assert _count_submissions() == 2


def test_blank_idempotency_key_inserts_twice(service_db):
    first = create_submission_sync(
        first_name="Ada",
        last_name="Lovelace",
        email="blank-key@example.com",
        idempotency_key="   ",
    )
    second = create_submission_sync(
        first_name="Ada",
        last_name="Lovelace",
        email="blank-key@example.com",
        idempotency_key="",
    )

    assert first.id != second.id
    assert _count_submissions() == 2
    assert _get_submission(first.id).idempotency_key is None
    assert _get_submission(second.id).idempotency_key is None


def test_new_idempotency_key_is_stored(service_db):
    submission = create_submission_sync(
        first_name="Ada",
        last_name="Lovelace",
        email="new-key@example.com",
        idempotency_key="new-key",
    )

    row = _get_submission(submission.id)
    assert row.idempotency_key == "new-key"


def test_same_idempotency_key_and_payload_returns_existing_submission(
    service_db, caplog
):
    caplog.set_level(logging.INFO)

    first = create_submission_sync(
        first_name="Grace",
        last_name="Hopper",
        email="reuse@example.com",
        idempotency_key="reuse-key",
    )
    second = create_submission_sync(
        first_name="Grace",
        last_name="Hopper",
        email="reuse@example.com",
        idempotency_key="reuse-key",
    )

    assert second.id == first.id
    assert _count_submissions() == 1

    records = _service_records(caplog)
    assert [record.event for record in records] == ["submission_idempotent_reuse"]
    assert records[0].idempotency_key == "reuse-key"
    assert records[0].submission_id == first.id
    assert records[0].form_type == "sync"
    _assert_no_pii_logged(caplog)


def test_same_idempotency_key_different_payload_raises_conflict(service_db, caplog):
    caplog.set_level(logging.INFO)
    create_submission_sync(
        first_name="Ada",
        last_name="Lovelace",
        email="conflict@example.com",
        idempotency_key="conflict-key",
    )

    with pytest.raises(IdempotencyConflict):
        create_submission_sync(
            first_name="Grace",
            last_name="Hopper",
            email="conflict@example.com",
            idempotency_key="conflict-key",
        )

    assert _count_submissions() == 1
    records = _service_records(caplog)
    assert [record.event for record in records] == ["submission_idempotency_conflict"]
    assert records[0].idempotency_key == "conflict-key"
    assert records[0].form_type == "sync"
    _assert_no_pii_logged(caplog)


def test_integrity_error_race_path_returns_matching_existing_submission(
    service_db, monkeypatch, caplog
):
    caplog.set_level(logging.INFO)
    original_commit = SQLAlchemySession.commit
    race_inserted = False

    def commit_with_race(session):
        nonlocal race_inserted
        should_insert_race = any(
            isinstance(item, Submission) and item.idempotency_key == "race-key"
            for item in session.new
        )
        if should_insert_race and not race_inserted:
            race_inserted = True
            with session_scope() as other_session:
                other_session.add(
                    Submission(
                        first_name="Katherine",
                        last_name="Johnson",
                        email="race@example.com",
                        form_type="sync",
                        status="processed",
                        idempotency_key="race-key",
                    )
                )
                original_commit(other_session)
        return original_commit(session)

    monkeypatch.setattr(SQLAlchemySession, "commit", commit_with_race)

    submission = create_submission_sync(
        first_name="Katherine",
        last_name="Johnson",
        email="race@example.com",
        idempotency_key="race-key",
    )

    assert _count_submissions() == 1
    assert submission.id == _get_submission(submission.id).id

    records = _service_records(caplog)
    assert [record.event for record in records] == [
        "submission_idempotent_race_resolved"
    ]
    assert records[0].idempotency_key == "race-key"
    assert records[0].submission_id == submission.id
    assert records[0].form_type == "sync"
    _assert_no_pii_logged(caplog)


def test_create_submission_sync_persists_key_and_sync_form_type(service_db):
    submission = create_submission_sync(
        first_name="Ada",
        last_name="Lovelace",
        email="sync-key@example.com",
        idempotency_key="sync-key",
    )

    row = _get_submission(submission.id)
    assert isinstance(submission, Submission)
    assert submission.id is not None
    assert row.idempotency_key == "sync-key"
    assert row.form_type == "sync"


def test_save_submission_from_worker_persists_key_and_async_form_type(service_db):
    submission = save_submission_from_worker(
        first_name="Grace",
        last_name="Hopper",
        email="worker-key@example.com",
        idempotency_key="worker-key",
    )

    row = _get_submission(submission.id)
    assert isinstance(submission, Submission)
    assert submission.id is not None
    assert row.idempotency_key == "worker-key"
    assert row.form_type == "async"


def test_list_submissions_applies_limit_and_desc_order(tmp_path):
    db_path = tmp_path / "services_limit.db"
    init_database(f"sqlite:///{db_path.as_posix()}")

    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with session_scope() as session:
        for idx in range(5):
            session.add(
                Submission(
                    first_name=f"F{idx}",
                    last_name=f"L{idx}",
                    email=f"user{idx}@example.com",
                    form_type="sync",
                    status="processed",
                    created_at=base_time + timedelta(seconds=idx),
                )
            )
        session.commit()

    rows = list_submissions(limit=2)
    assert len(rows) == 2
    assert [row.email for row in rows] == ["user4@example.com", "user3@example.com"]
