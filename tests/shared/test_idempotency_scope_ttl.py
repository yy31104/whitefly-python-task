"""Idempotency keys are scoped per client and expire after a TTL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from shared.db import init_database, session_scope
from shared.idempotency import (
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    idempotency_ttl,
    is_expired,
    sanitize_idempotency_key,
    scope_idempotency_key,
)
from shared.models import IdempotencyClaim, Submission
from shared.services import (
    IdempotencyKeyMismatch,
    IdempotencyRequestInFlight,
    create_submission_sync,
    enqueue_submission_async,
)


@pytest.fixture
def db(tmp_path):
    init_database(f"sqlite:///{(tmp_path / 'scope_ttl.db').as_posix()}")


@pytest.fixture
def enqueued(monkeypatch):
    calls: list[dict] = []

    def fake_apply_async(kwargs=None, task_id=None, **_options):
        calls.append({"kwargs": kwargs, "task_id": task_id})

    from worker.tasks import save_async_submission_task

    monkeypatch.setattr(save_async_submission_task, "apply_async", fake_apply_async)
    return calls


def _sync(first: str, *, key: str, client: str | None):
    return create_submission_sync(
        first_name=first,
        last_name="Hopper",
        email=f"{first.lower()}@example.com",
        idempotency_key=key,
        client_scope=client,
    )


def _async(first: str, *, key: str, client: str | None):
    return enqueue_submission_async(
        first_name=first,
        last_name="Hopper",
        email=f"{first.lower()}@example.com",
        idempotency_key=key,
        client_scope=client,
    )


def _age_submissions(seconds: int) -> None:
    with session_scope() as session:
        session.execute(
            update(Submission).values(
                created_at=datetime.now(UTC) - timedelta(seconds=seconds)
            )
        )
        session.commit()


def _age_claims(seconds: int) -> None:
    with session_scope() as session:
        session.execute(
            update(IdempotencyClaim).values(
                created_at=datetime.now(UTC) - timedelta(seconds=seconds)
            )
        )
        session.commit()


def _keyed_rows() -> list[Submission]:
    with session_scope() as session:
        return (
            session.query(Submission)
            .filter(Submission.idempotency_key.is_not(None))
            .order_by(Submission.id)
            .all()
        )


# --- scope_idempotency_key ----------------------------------------------------


def test_scoped_key_differs_per_client_and_is_stable():
    a = scope_idempotency_key("order-1", "10.0.0.1")
    assert a == scope_idempotency_key("order-1", "10.0.0.1")
    assert a != scope_idempotency_key("order-1", "10.0.0.2")
    assert a != scope_idempotency_key("order-2", "10.0.0.1")


def test_scoped_key_fits_column_and_hides_inputs():
    scoped = scope_idempotency_key("k" * 64, "2001:db8::1")
    assert len(scoped) == 64
    assert sanitize_idempotency_key(scoped) == scoped
    assert "2001:db8::1" not in scoped


def test_scope_boundary_cannot_be_shifted():
    # "a" + "bc" and "ab" + "c" must not hash the same.
    assert scope_idempotency_key("bc", "a") != scope_idempotency_key("c", "ab")


def test_unscoped_and_empty_keys():
    assert scope_idempotency_key("raw", None) == "raw"
    assert scope_idempotency_key(None, "10.0.0.1") is None
    assert scope_idempotency_key("", "10.0.0.1") is None


# --- TTL ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        (None, DEFAULT_IDEMPOTENCY_TTL_SECONDS),
        ("3600", 3600),
        ("0", DEFAULT_IDEMPOTENCY_TTL_SECONDS),
        ("-5", DEFAULT_IDEMPOTENCY_TTL_SECONDS),
        ("soon", DEFAULT_IDEMPOTENCY_TTL_SECONDS),
    ],
)
def test_ttl_from_environment(monkeypatch, raw, seconds):
    if raw is None:
        monkeypatch.delenv("IDEMPOTENCY_KEY_TTL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("IDEMPOTENCY_KEY_TTL_SECONDS", raw)
    assert idempotency_ttl() == timedelta(seconds=seconds)


def test_is_expired_handles_naive_sqlite_datetimes(monkeypatch):
    monkeypatch.setenv("IDEMPOTENCY_KEY_TTL_SECONDS", "60")
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert is_expired(datetime(2026, 1, 1, 11, 59, 0), now=now)
    assert not is_expired(datetime(2026, 1, 1, 11, 59, 1), now=now)
    assert not is_expired(None, now=now)


# --- per-client scope through the service layer -----------------------------


def test_sync_same_key_from_two_clients_does_not_collide(db):
    first = _sync("Grace", key="shared-key", client="10.0.0.1")
    second = _sync("Alan", key="shared-key", client="10.0.0.2")
    assert first.id != second.id
    assert len(_keyed_rows()) == 2


def test_sync_same_client_still_gets_422_and_replay(db):
    first = _sync("Grace", key="mine", client="10.0.0.1")
    assert _sync("Grace", key="mine", client="10.0.0.1").id == first.id
    with pytest.raises(IdempotencyKeyMismatch):
        _sync("Alan", key="mine", client="10.0.0.1")


def test_async_same_key_from_two_clients_enqueues_both(db, enqueued):
    _async("Grace", key="shared-key", client="10.0.0.1")
    _async("Alan", key="shared-key", client="10.0.0.2")
    assert len(enqueued) == 2
    keys = {call["kwargs"]["idempotency_key"] for call in enqueued}
    assert keys == {
        scope_idempotency_key("shared-key", "10.0.0.1"),
        scope_idempotency_key("shared-key", "10.0.0.2"),
    }


def test_async_same_client_same_key_is_still_409(db, enqueued):
    _async("Grace", key="mine", client="10.0.0.1")
    with pytest.raises(IdempotencyRequestInFlight):
        _async("Grace", key="mine", client="10.0.0.1")
    assert len(enqueued) == 1


# --- expiry ------------------------------------------------------------------


def test_sync_key_is_reusable_after_ttl(db, monkeypatch):
    monkeypatch.setenv("IDEMPOTENCY_KEY_TTL_SECONDS", "60")
    old = _sync("Grace", key="k", client="10.0.0.1")
    _age_submissions(61)

    new = _sync("Alan", key="k", client="10.0.0.1")

    assert new.id != old.id
    rows = _keyed_rows()
    assert [row.id for row in rows] == [new.id]  # the old row released its key


def test_sync_key_inside_ttl_is_still_bound(db, monkeypatch):
    monkeypatch.setenv("IDEMPOTENCY_KEY_TTL_SECONDS", "60")
    _sync("Grace", key="k", client="10.0.0.1")
    _age_submissions(30)
    with pytest.raises(IdempotencyKeyMismatch):
        _sync("Alan", key="k", client="10.0.0.1")


def test_async_expired_claim_is_taken_over(db, enqueued, monkeypatch):
    monkeypatch.setenv("IDEMPOTENCY_KEY_TTL_SECONDS", "60")
    first_task = _async("Grace", key="k", client="10.0.0.1")
    _age_claims(61)

    # Same key, even a different payload: after the TTL it is a new request.
    second_task = _async("Alan", key="k", client="10.0.0.1")

    assert second_task != first_task
    assert len(enqueued) == 2
    with session_scope() as session:
        claim = session.get(IdempotencyClaim, scope_idempotency_key("k", "10.0.0.1"))
        assert claim.task_id == second_task


def test_async_claim_inside_ttl_is_still_in_flight(db, enqueued, monkeypatch):
    monkeypatch.setenv("IDEMPOTENCY_KEY_TTL_SECONDS", "60")
    _async("Grace", key="k", client="10.0.0.1")
    _age_claims(30)
    with pytest.raises(IdempotencyRequestInFlight):
        _async("Grace", key="k", client="10.0.0.1")
    assert len(enqueued) == 1


def test_sync_ignores_expired_async_claim(db, enqueued, monkeypatch):
    monkeypatch.setenv("IDEMPOTENCY_KEY_TTL_SECONDS", "60")
    _async("Grace", key="k", client="10.0.0.1")  # stays "queued" forever
    _age_claims(61)
    submission = _sync("Grace", key="k", client="10.0.0.1")
    assert submission.id is not None
