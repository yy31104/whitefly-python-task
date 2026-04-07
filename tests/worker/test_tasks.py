from __future__ import annotations

from pathlib import Path

import worker.tasks as worker_tasks
from shared.db import session_scope
from shared.models import Submission


def test_save_async_submission_task_persists_record(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "worker_task.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(worker_tasks, "_db_ready", False)

    submission_id = worker_tasks.save_async_submission_task.run(
        first_name="Grace",
        last_name="Hopper",
        email="grace@example.com",
    )

    with session_scope() as session:
        row = session.query(Submission).filter_by(id=submission_id).one()

    assert row.first_name == "Grace"
    assert row.last_name == "Hopper"
    assert row.email == "grace@example.com"
    assert row.form_type == "async"
    assert row.status == "processed"
