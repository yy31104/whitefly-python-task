from __future__ import annotations

from datetime import datetime, timedelta, timezone

from shared.db import init_database, session_scope
from shared.models import Submission
from shared.services import list_submissions


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
