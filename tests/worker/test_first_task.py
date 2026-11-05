from __future__ import annotations

import multiprocessing
import os
import uuid

import pytest
from sqlalchemy import create_engine, select

from shared.db import Base
from shared.models import Submission, TaskStatus


def _run_first_task(database_url, task_id, before_insert, finish_insert):
    from shared import db
    from worker import tasks

    # A prefork child can receive its first task before any app initialised it.
    db._engine = None
    db.SessionLocal.configure(bind=None)
    tasks._db_ready = False
    os.environ["DATABASE_URL"] = database_url
    real_save = tasks.save_submission_from_worker

    def pause_before_insert(**kwargs):
        before_insert.set()
        assert finish_insert.wait(15), "parent did not release the worker"
        return real_save(**kwargs)

    tasks.save_submission_from_worker = pause_before_insert
    tasks.save_async_submission_task.apply(
        kwargs={
            "first_name": "First",
            "last_name": "Task",
            "email": "first-task@example.com",
        },
        task_id=task_id,
        throw=True,
    )


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="Celery prefork requires a platform with fork",
)
def test_first_task_in_uninitialised_child_publishes_processing(tmp_path):
    database_url = os.environ.get(
        "TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'first-task.db'}"
    )
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    task_id = uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            TaskStatus.__table__.insert().values(task_id=task_id, status="queued")
        )
    engine.dispose()

    context = multiprocessing.get_context("fork")
    before_insert = context.Event()
    finish_insert = context.Event()
    child = context.Process(
        target=_run_first_task,
        args=(database_url, task_id, before_insert, finish_insert),
    )
    child.start()
    try:
        assert before_insert.wait(15), "worker did not reach submission persistence"
        with engine.connect() as connection:
            status = connection.scalar(
                select(TaskStatus.status).where(TaskStatus.task_id == task_id)
            )
        assert status == "processing"
    finally:
        finish_insert.set()
        child.join(15)
        if child.is_alive():
            child.terminate()
            child.join(5)
        engine.dispose()

    assert child.exitcode == 0
    with engine.connect() as connection:
        row = connection.execute(
            select(TaskStatus.status, TaskStatus.submission_id).where(
                TaskStatus.task_id == task_id
            )
        ).one()
        assert row.status == "processed"
        assert (
            connection.scalar(
                select(Submission.id).where(Submission.id == row.submission_id)
            )
            == row.submission_id
        )
    engine.dispose()
