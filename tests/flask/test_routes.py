from __future__ import annotations

import logging
import re
import uuid

import pytest

from flask_app.app import create_app
from shared.db import session_scope
from shared.idempotency import scope_idempotency_key
from shared.logging import get_request_id
from shared.models import Submission
from shared.rate_limit import RateLimitExceeded
from shared.services import QueueUnavailable
from shared.task_status import mark_task_queued, record_task_status

_IDEMPOTENCY_FIELD_RE = re.compile(r'name="idempotency_key" value="([^"]+)"')


@pytest.fixture
def app(tmp_path):
    db_path = tmp_path / "m1_test.db"
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        }
    )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _count_submissions() -> int:
    with session_scope() as session:
        return session.query(Submission).count()


def _submission_by_email(email: str) -> Submission:
    with session_scope() as session:
        return session.query(Submission).filter_by(email=email).one()


def _extract_hidden_idempotency_key(response) -> str:
    match = _IDEMPOTENCY_FIELD_RE.search(response.get_data(as_text=True))
    assert match is not None
    return match.group(1)


def _assert_async_status_payload(payload: dict, *, task_id: str, status: str) -> None:
    assert payload["task_id"] == task_id
    assert payload["status"] == status
    assert set(payload) == {
        "task_id",
        "status",
        "submission_id",
        "error_type",
        "retries",
        "created_at",
        "updated_at",
    }
    assert isinstance(payload["retries"], int)
    assert isinstance(payload["created_at"], str)
    assert isinstance(payload["updated_at"], str)


def test_home_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Hello World" in response.data


def test_healthz_returns_ok_json(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert "application/json" in response.content_type
    assert response.get_json() == {"status": "ok"}
    assert '{"status":"ok"}' in response.get_data(as_text=True).replace(" ", "")


def test_response_includes_generated_request_id(client):
    response = client.get("/healthz")

    request_id = response.headers["X-Request-ID"]
    assert uuid.UUID(request_id)


def test_client_supplied_request_id_is_echoed(client):
    response = client.get(
        "/healthz", headers={"X-Request-ID": "flask-client-request-id"}
    )

    assert response.headers["X-Request-ID"] == "flask-client-request-id"


def test_malicious_request_id_is_replaced_and_not_logged(client, caplog):
    caplog.set_level(logging.INFO)
    malicious_request_id = "evil request SECRET_KEY"

    response = client.post(
        "/sync-form",
        data={
            "first_name": "NoLogFirst",
            "last_name": "NoLogLast",
            "email": "nolog-malicious-flask@example.com",
            "honeypot": "",
        },
        headers={"X-Request-ID": malicious_request_id},
        follow_redirects=False,
    )

    assert response.status_code == 302
    safe_request_id = response.headers["X-Request-ID"]
    assert safe_request_id != malicious_request_id
    assert uuid.UUID(safe_request_id)

    records = [record for record in caplog.records if record.name == "flask_app.app"]
    assert records
    assert all(record.request_id == safe_request_id for record in records)

    rendered_logs = "\n".join(str(record.__dict__) for record in records)
    assert malicious_request_id not in rendered_logs
    assert "nolog-malicious-flask@example.com" not in rendered_logs
    assert "NoLogFirst" not in rendered_logs
    assert "NoLogLast" not in rendered_logs
    assert "SECRET_KEY" not in rendered_logs


def test_sync_form_get(client):
    response = client.get("/sync-form")
    assert response.status_code == 200
    assert b"Sync Submission Form" in response.data


def test_sync_form_get_renders_hidden_idempotency_key(client):
    response = client.get("/sync-form")

    key = _extract_hidden_idempotency_key(response)
    assert uuid.UUID(key).version == 4


def test_async_form_get(client):
    response = client.get("/async-form")
    assert response.status_code == 200
    assert b"Async Submission Form" in response.data
    assert b'id="async-status"' not in response.data


def test_async_form_get_renders_hidden_idempotency_key(client):
    response = client.get("/async-form")

    key = _extract_hidden_idempotency_key(response)
    assert uuid.UUID(key).version == 4


def test_valid_post_creates_record(client):
    response = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "honeypot": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/submissions")
    assert _count_submissions() == 1


def test_sync_double_post_same_idempotency_key_creates_one_record(client):
    form_data = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada-idempotent@example.com",
        "honeypot": "",
        "idempotency_key": "flask-sync-same-key",
    }

    first = client.post("/sync-form", data=form_data, follow_redirects=False)
    second = client.post("/sync-form", data=form_data, follow_redirects=False)

    assert first.status_code == 302
    assert second.status_code == 302
    assert _count_submissions() == 1


def test_sync_idempotency_header_overrides_form_key(client):
    response = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "flask-header@example.com",
            "honeypot": "",
            "idempotency_key": "flask-form-key",
        },
        headers={"Idempotency-Key": "flask-header-key"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    row = _submission_by_email("flask-header@example.com")
    # The stored key is scoped to the client that sent it.
    assert row.idempotency_key == scope_idempotency_key("flask-header-key", "127.0.0.1")


def test_sync_malformed_idempotency_key_is_ignored(client):
    response = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "flask-malformed@example.com",
            "honeypot": "",
            "idempotency_key": "bad key",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    row = _submission_by_email("flask-malformed@example.com")
    assert row.idempotency_key is None


def test_sync_same_idempotency_key_different_payload_returns_422(client):
    first = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "flask-conflict@example.com",
            "honeypot": "",
            "idempotency_key": "flask-conflict-key",
        },
        follow_redirects=False,
    )
    second = client.post(
        "/sync-form",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "flask-conflict@example.com",
            "honeypot": "",
            "idempotency_key": "flask-conflict-key",
        },
        follow_redirects=False,
    )

    assert first.status_code == 302
    assert second.status_code == 422
    assert b"already used for a different submission" in second.data
    assert uuid.UUID(_extract_hidden_idempotency_key(second)).version == 4
    assert _count_submissions() == 1


def test_valid_async_post_queues_task(client, monkeypatch):
    calls: list[dict[str, str]] = []

    class DummyTaskResult:
        id = "task-123"

    def fake_delay(
        *, first_name: str, last_name: str, email: str, request_id: str | None = None
    ):
        calls.append(
            {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "request_id": request_id,
            }
        )
        return DummyTaskResult()

    from worker.tasks import save_async_submission_task

    monkeypatch.setattr(save_async_submission_task, "delay", fake_delay)

    response = client.post(
        "/async-form",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "honeypot": "",
        },
        headers={"X-Request-ID": "flask-async-request-id"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Submission queued. Task ID: task-123" in response.data
    body = response.get_data(as_text=True)
    assert 'id="async-status"' in body
    assert 'data-status-url="/async-status/task-123"' in body
    assert "Grace" not in body
    assert "Hopper" not in body
    assert "grace@example.com" not in body
    assert calls == [
        {
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "request_id": "flask-async-request-id",
        }
    ]
    assert _count_submissions() == 0


def test_async_form_malformed_task_id_renders_no_status_region(client):
    response = client.get("/async-form?task_id=bad%20task%20SECRET_KEY")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="async-status"' not in body
    assert "bad task SECRET_KEY" not in body
    assert "SECRET_KEY" not in body


def test_async_form_status_region_uses_flask_prefix(tmp_path):
    db_path = tmp_path / "flask_prefixed_polling.db"
    prefixed_app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "FLASK_URL_PREFIX": "/flask",
        }
    )

    response = prefixed_app.test_client().get(
        "/flask/async-form?task_id=flask-prefix-poll-task"
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="async-status"' in body
    assert 'data-status-url="/flask/async-status/flask-prefix-poll-task"' in body


def test_async_post_with_idempotency_key_passes_key_to_task(client, monkeypatch):
    calls: list[dict[str, str]] = []

    def fake_apply_async(kwargs=None, task_id=None, **_options):
        calls.append({**kwargs, "_task_id": task_id})

    from worker.tasks import save_async_submission_task

    monkeypatch.setattr(save_async_submission_task, "apply_async", fake_apply_async)

    response = client.post(
        "/async-form",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "flask-async-key@example.com",
            "honeypot": "",
            "idempotency_key": "flask-async-key",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert calls[0]["idempotency_key"] == scope_idempotency_key(
        "flask-async-key", "127.0.0.1"
    )


def test_async_post_without_idempotency_key_omits_task_kwarg(client, monkeypatch):
    calls: list[dict[str, str]] = []

    class DummyTaskResult:
        id = "task-without-key"

    def fake_delay(**kwargs):
        calls.append(kwargs)
        return DummyTaskResult()

    from worker.tasks import save_async_submission_task

    monkeypatch.setattr(save_async_submission_task, "delay", fake_delay)

    response = client.post(
        "/async-form",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "flask-async-no-key@example.com",
            "honeypot": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "idempotency_key" not in calls[0]


def test_async_status_returns_queued_json_without_pii(client):
    mark_task_queued("flask-task-queued")

    response = client.get("/async-status/flask-task-queued")

    assert response.status_code == 200
    assert "application/json" in response.content_type
    payload = response.get_json()
    _assert_async_status_payload(
        payload,
        task_id="flask-task-queued",
        status="queued",
    )
    assert payload["submission_id"] is None
    assert payload["error_type"] is None
    body = response.get_data(as_text=True)
    assert "Ada" not in body
    assert "Lovelace" not in body
    assert "ada@example.com" not in body
    assert "payload" not in body


def test_async_status_returns_processed_json(client):
    record_task_status(
        "flask-task-processed",
        "processed",
        submission_id=123,
        retries=1,
    )

    response = client.get("/async-status/flask-task-processed")

    assert response.status_code == 200
    payload = response.get_json()
    _assert_async_status_payload(
        payload,
        task_id="flask-task-processed",
        status="processed",
    )
    assert payload["submission_id"] == 123
    assert payload["error_type"] is None
    assert payload["retries"] == 1


def test_async_status_returns_conflict_json(client):
    record_task_status(
        "flask-task-conflict",
        "conflict",
        error_type="IdempotencyConflict",
        retries=0,
    )

    response = client.get("/async-status/flask-task-conflict")

    assert response.status_code == 200
    payload = response.get_json()
    _assert_async_status_payload(
        payload,
        task_id="flask-task-conflict",
        status="conflict",
    )
    assert payload["submission_id"] is None
    assert payload["error_type"] == "IdempotencyConflict"


def test_async_status_unknown_task_returns_404_json(client):
    response = client.get("/async-status/flask-task-missing")

    assert response.status_code == 404
    assert "application/json" in response.content_type
    assert response.get_json() == {"detail": "Task status not found."}


def test_async_status_malformed_task_id_returns_400_json(client):
    response = client.get("/async-status/bad%20task")

    assert response.status_code == 400
    assert "application/json" in response.content_type
    assert response.get_json() == {"detail": "Invalid task id."}


def test_async_status_route_works_with_flask_prefix(tmp_path):
    db_path = tmp_path / "flask_prefixed_status.db"
    prefixed_app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "FLASK_URL_PREFIX": "/flask",
        }
    )
    mark_task_queued("flask-prefix-task")

    response = prefixed_app.test_client().get("/flask/async-status/flask-prefix-task")

    assert response.status_code == 200
    assert response.get_json()["task_id"] == "flask-prefix-task"


def test_form_post_logs_request_id_without_raw_pii(client, caplog):
    caplog.set_level(logging.INFO)

    response = client.post(
        "/sync-form",
        data={
            "first_name": "NoLogFirst",
            "last_name": "NoLogLast",
            "email": "nolog-flask@example.com",
            "honeypot": "",
        },
        headers={"X-Request-ID": "flask-log-request-id"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    records = [record for record in caplog.records if record.name == "flask_app.app"]
    assert [record.event for record in records] == ["form_post_start", "form_post_end"]
    assert all(record.request_id == "flask-log-request-id" for record in records)

    rendered_logs = "\n".join(str(record.__dict__) for record in records)
    assert "nolog-flask@example.com" not in rendered_logs
    assert "NoLogFirst" not in rendered_logs
    assert "NoLogLast" not in rendered_logs
    assert "test-secret" not in rendered_logs
    assert "SECRET_KEY" not in rendered_logs


def test_async_post_queue_unavailable_returns_503(client, monkeypatch):
    from flask_app.app import routes

    def fake_enqueue_submission_async(**kwargs):
        raise QueueUnavailable(
            "Queue is temporarily unavailable. Please try again shortly."
        )

    monkeypatch.setattr(
        routes, "enqueue_submission_async", fake_enqueue_submission_async
    )

    response = client.post(
        "/async-form",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "honeypot": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert (
        b"Queue is temporarily unavailable. Please try again shortly." in response.data
    )
    assert _count_submissions() == 0


def test_sync_form_rate_limit_returns_429(client, monkeypatch):
    from flask_app.app import routes

    def fake_rate_limit(**kwargs):
        raise RateLimitExceeded(retry_after=15)

    monkeypatch.setattr(routes, "enforce_rate_limit", fake_rate_limit)

    response = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "honeypot": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 429
    assert b"Too many requests." in response.data
    assert response.headers["Retry-After"] == "15"
    assert _count_submissions() == 0


def test_rate_limit_prefers_x_real_ip(client, monkeypatch):
    from flask_app.app import routes

    captured: dict[str, str] = {}

    def fake_rate_limit(**kwargs):
        captured.update(kwargs)
        raise RateLimitExceeded(retry_after=5)

    monkeypatch.setattr(routes, "enforce_rate_limit", fake_rate_limit)

    response = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "honeypot": "",
        },
        headers={
            "X-Real-IP": "203.0.113.10",
            "X-Forwarded-For": "198.51.100.1, 198.51.100.2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 429
    assert captured["identifier"] == "203.0.113.10"
    assert _count_submissions() == 0


def test_async_form_rate_limit_returns_429(client, monkeypatch):
    from flask_app.app import routes

    def fake_rate_limit(**kwargs):
        raise RateLimitExceeded(retry_after=20)

    monkeypatch.setattr(routes, "enforce_rate_limit", fake_rate_limit)

    response = client.post(
        "/async-form",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "honeypot": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 429
    assert b"Too many requests." in response.data
    assert response.headers["Retry-After"] == "20"
    assert _count_submissions() == 0


def test_invalid_post_shows_error(client):
    response = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "invalid-email",
            "honeypot": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert b"Enter a valid email address." in response.data
    assert _count_submissions() == 0


def test_invalid_async_post_shows_error(client):
    response = client.post(
        "/async-form",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "invalid-email",
            "honeypot": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert b"Enter a valid email address." in response.data
    assert _count_submissions() == 0


def test_honeypot_post_rejected(client):
    response = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "honeypot": "bot-value",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert b"Spam detected." in response.data
    assert _count_submissions() == 0


def test_honeypot_async_post_rejected(client):
    response = client.post(
        "/async-form",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "honeypot": "bot-value",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert b"Spam detected." in response.data
    assert _count_submissions() == 0


def test_submissions_page_renders(client):
    response = client.get("/submissions")
    assert response.status_code == 200
    assert b"Saved Submissions" in response.data


def test_submissions_page_uses_configured_limit(tmp_path, monkeypatch):
    db_path = tmp_path / "submissions_limit.db"
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "SUBMISSIONS_PAGE_LIMIT": 7,
        }
    )
    client = app.test_client()

    from flask_app.app import routes

    captured: dict[str, int] = {}

    def fake_list_submissions(*, limit: int):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(routes, "list_submissions", fake_list_submissions)

    response = client.get("/submissions")
    assert response.status_code == 200
    assert captured["limit"] == 7


def test_submissions_page_disabled_in_production(tmp_path):
    db_path = tmp_path / "prod_disabled.db"
    app = create_app(
        {
            "TESTING": True,
            "APP_ENV": "production",
            "SECRET_KEY": "prod-test-secret",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "ENABLE_SUBMISSIONS_PAGE": False,
        }
    )
    client = app.test_client()
    response = client.get("/submissions")
    assert response.status_code == 404


def test_production_requires_secret_key(tmp_path):
    db_path = tmp_path / "prod_missing_secret.db"
    with pytest.raises(RuntimeError, match="SECRET_KEY must be explicitly set"):
        create_app(
            {
                "TESTING": True,
                "APP_ENV": "production",
                "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            }
        )


def test_request_id_context_clears_after_error(tmp_path):
    db_path = tmp_path / "flask_error_cleanup.db"
    app = create_app(
        {
            "TESTING": False,
            "PROPAGATE_EXCEPTIONS": False,
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        }
    )

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    response = app.test_client().get(
        "/boom", headers={"X-Request-ID": "flask-error-request-id"}
    )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "flask-error-request-id"
    assert get_request_id() is None


def test_async_same_key_twice_returns_409_while_in_flight(client, monkeypatch):
    from worker.tasks import save_async_submission_task

    calls: list[dict] = []
    monkeypatch.setattr(
        save_async_submission_task,
        "apply_async",
        lambda kwargs=None, task_id=None, **_: calls.append(kwargs),
    )
    data = {
        "first_name": "Grace",
        "last_name": "Hopper",
        "email": "flask-in-flight@example.com",
        "honeypot": "",
        "idempotency_key": "flask-in-flight-key",
    }

    first = client.post("/async-form", data=data, follow_redirects=False)
    second = client.post("/async-form", data=data, follow_redirects=False)

    assert first.status_code == 302
    assert second.status_code == 409
    assert b"still being processed" in second.data
    assert len(calls) == 1


def test_async_same_key_different_payload_returns_422(client, monkeypatch):
    from worker.tasks import save_async_submission_task

    monkeypatch.setattr(
        save_async_submission_task,
        "apply_async",
        lambda kwargs=None, task_id=None, **_: None,
    )
    base = {
        "last_name": "Hopper",
        "email": "flask-reuse@example.com",
        "honeypot": "",
        "idempotency_key": "flask-reuse-key",
    }

    first = client.post("/async-form", data={**base, "first_name": "Grace"})
    second = client.post("/async-form", data={**base, "first_name": "Ada"})

    assert first.status_code == 302
    assert second.status_code == 422
    assert b"already used for a different submission" in second.data


def test_same_idempotency_key_from_two_clients_does_not_collide(client):
    for ip, first_name in (("10.0.0.1", "Grace"), ("10.0.0.2", "Alan")):
        response = client.post(
            "/sync-form",
            data={
                "first_name": first_name,
                "last_name": "Hopper",
                "email": f"{first_name.lower()}@example.com",
            },
            headers={"Idempotency-Key": "shared-key", "X-Real-IP": ip},
        )
        assert response.status_code == 302
    assert _count_submissions() == 2
