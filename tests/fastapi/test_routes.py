from __future__ import annotations

import logging
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from fastapi_app.app.main import create_app
from shared.db import session_scope
from shared.logging import get_request_id
from shared.models import Submission
from shared.rate_limit import RateLimitExceeded
from shared.services import QueueUnavailable

_IDEMPOTENCY_FIELD_RE = re.compile(r'name="idempotency_key" value="([^"]+)"')


@pytest.fixture
def app(tmp_path):
    db_path = tmp_path / "m3_fastapi_test.db"
    return create_app(database_url=f"sqlite:///{db_path.as_posix()}")


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _count_submissions() -> int:
    with session_scope() as session:
        return session.query(Submission).count()


def _submission_by_email(email: str) -> Submission:
    with session_scope() as session:
        return session.query(Submission).filter_by(email=email).one()


def _extract_hidden_idempotency_key(response) -> str:
    match = _IDEMPOTENCY_FIELD_RE.search(response.text)
    assert match is not None
    return match.group(1)


def test_home_page(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "Hello World" in response.text


def test_healthz_returns_ok_json(client: TestClient):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    assert response.json() == {"status": "ok"}
    assert '{"status":"ok"}' in response.text.replace(" ", "")


def test_response_includes_generated_request_id(client: TestClient):
    response = client.get("/healthz")

    request_id = response.headers["X-Request-ID"]
    assert uuid.UUID(request_id)


def test_client_supplied_request_id_is_echoed(client: TestClient):
    response = client.get(
        "/healthz", headers={"X-Request-ID": "fastapi-client-request-id"}
    )

    assert response.headers["X-Request-ID"] == "fastapi-client-request-id"


def test_malicious_request_id_is_replaced_and_not_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO)
    malicious_request_id = "evil request SECRET_KEY"

    response = client.post(
        "/sync-form",
        data={
            "first_name": "NoLogFirst",
            "last_name": "NoLogLast",
            "email": "nolog-malicious-fastapi@example.com",
            "honeypot": "",
        },
        headers={"X-Request-ID": malicious_request_id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    safe_request_id = response.headers["X-Request-ID"]
    assert safe_request_id != malicious_request_id
    assert uuid.UUID(safe_request_id)

    records = [
        record for record in caplog.records if record.name == "fastapi_app.app.main"
    ]
    assert records
    assert all(record.request_id == safe_request_id for record in records)

    rendered_logs = "\n".join(str(record.__dict__) for record in records)
    assert malicious_request_id not in rendered_logs
    assert "nolog-malicious-fastapi@example.com" not in rendered_logs
    assert "NoLogFirst" not in rendered_logs
    assert "NoLogLast" not in rendered_logs
    assert "SECRET_KEY" not in rendered_logs


def test_sync_form_get(client: TestClient):
    response = client.get("/sync-form")
    assert response.status_code == 200
    assert "Sync Submission Form" in response.text


def test_sync_form_get_renders_hidden_idempotency_key(client: TestClient):
    response = client.get("/sync-form")

    key = _extract_hidden_idempotency_key(response)
    assert uuid.UUID(key).version == 4


def test_async_form_get(client: TestClient):
    response = client.get("/async-form")
    assert response.status_code == 200
    assert "Async Submission Form" in response.text


def test_async_form_get_renders_hidden_idempotency_key(client: TestClient):
    response = client.get("/async-form")

    key = _extract_hidden_idempotency_key(response)
    assert uuid.UUID(key).version == 4


def test_valid_sync_post_creates_record(client: TestClient):
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

    assert response.status_code == 303
    assert response.headers["location"].startswith("/submissions")
    assert _count_submissions() == 1


def test_sync_double_post_same_idempotency_key_creates_one_record(
    client: TestClient,
):
    form_data = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "fastapi-idempotent@example.com",
        "honeypot": "",
        "idempotency_key": "fastapi-sync-same-key",
    }

    first = client.post("/sync-form", data=form_data, follow_redirects=False)
    second = client.post("/sync-form", data=form_data, follow_redirects=False)

    assert first.status_code == 303
    assert second.status_code == 303
    assert _count_submissions() == 1


def test_sync_idempotency_header_overrides_form_key(client: TestClient):
    response = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "fastapi-header@example.com",
            "honeypot": "",
            "idempotency_key": "fastapi-form-key",
        },
        headers={"Idempotency-Key": "fastapi-header-key"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    row = _submission_by_email("fastapi-header@example.com")
    assert row.idempotency_key == "fastapi-header-key"


def test_sync_malformed_idempotency_key_is_ignored(client: TestClient):
    response = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "fastapi-malformed@example.com",
            "honeypot": "",
            "idempotency_key": "bad key",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    row = _submission_by_email("fastapi-malformed@example.com")
    assert row.idempotency_key is None


def test_sync_same_idempotency_key_different_payload_returns_409(
    client: TestClient,
):
    first = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "fastapi-conflict@example.com",
            "honeypot": "",
            "idempotency_key": "fastapi-conflict-key",
        },
        follow_redirects=False,
    )
    second = client.post(
        "/sync-form",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "fastapi-conflict@example.com",
            "honeypot": "",
            "idempotency_key": "fastapi-conflict-key",
        },
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert second.status_code == 409
    assert "Submission conflicts with a previous idempotency key." in second.text
    assert uuid.UUID(_extract_hidden_idempotency_key(second)).version == 4
    assert _count_submissions() == 1


def test_invalid_sync_post_shows_error(client: TestClient):
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
    assert "Enter a valid email address." in response.text
    assert _count_submissions() == 0


def test_async_post_with_idempotency_key_passes_key_to_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    calls: list[dict[str, str]] = []

    class DummyTaskResult:
        id = "task-fastapi-with-key"

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
            "email": "fastapi-async-key@example.com",
            "honeypot": "",
            "idempotency_key": "fastapi-async-key",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls[0]["idempotency_key"] == "fastapi-async-key"


def test_async_post_without_idempotency_key_omits_task_kwarg(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    calls: list[dict[str, str]] = []

    class DummyTaskResult:
        id = "task-fastapi-without-key"

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
            "email": "fastapi-async-no-key@example.com",
            "honeypot": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "idempotency_key" not in calls[0]


def test_valid_async_post_queues_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    calls: list[dict[str, str]] = []

    class DummyTaskResult:
        id = "task-fastapi-123"

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
        headers={"X-Request-ID": "fastapi-async-request-id"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Submission queued. Task ID: task-fastapi-123" in response.text
    assert calls == [
        {
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
            "request_id": "fastapi-async-request-id",
        }
    ]
    assert _count_submissions() == 0


def test_form_post_logs_request_id_without_raw_pii(
    client: TestClient, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO)

    response = client.post(
        "/sync-form",
        data={
            "first_name": "NoLogFirst",
            "last_name": "NoLogLast",
            "email": "nolog-fastapi@example.com",
            "honeypot": "",
        },
        headers={"X-Request-ID": "fastapi-log-request-id"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    records = [
        record for record in caplog.records if record.name == "fastapi_app.app.main"
    ]
    assert [record.event for record in records] == ["form_post_start", "form_post_end"]
    assert all(record.request_id == "fastapi-log-request-id" for record in records)

    rendered_logs = "\n".join(str(record.__dict__) for record in records)
    assert "nolog-fastapi@example.com" not in rendered_logs
    assert "NoLogFirst" not in rendered_logs
    assert "NoLogLast" not in rendered_logs
    assert "SECRET_KEY" not in rendered_logs


def test_prefixed_form_post_logs_request_id(tmp_path, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'fastapi_prefixed_logging.db').as_posix()}",
        url_prefix="/fastapi",
    )

    with TestClient(app) as prefixed_client:
        response = prefixed_client.post(
            "/fastapi/sync-form",
            data={
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada-prefixed@example.com",
                "honeypot": "",
            },
            headers={"X-Request-ID": "fastapi-prefix-request-id"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    records = [
        record for record in caplog.records if record.name == "fastapi_app.app.main"
    ]
    assert [record.event for record in records] == ["form_post_start", "form_post_end"]
    assert all(record.request_id == "fastapi-prefix-request-id" for record in records)
    assert all(record.path == "/fastapi/sync-form" for record in records)


def test_unhandled_exception_response_includes_safe_request_id_and_clears_context(
    tmp_path,
):
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'fastapi_exception_cleanup.db').as_posix()}"
    )

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get(
            "/boom",
            headers={"X-Request-ID": "unsafe request id"},
        )

    assert response.status_code == 500
    request_id = response.headers["X-Request-ID"]
    assert request_id != "unsafe request id"
    assert uuid.UUID(request_id)
    assert get_request_id() is None


def test_unhandled_exception_preserves_safe_request_id_and_clears_context(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'fastapi_exception_safe_request_id.db').as_posix()}"
    )

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get(
            "/boom",
            headers={"X-Request-ID": "good-id-123"},
        )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "good-id-123"
    assert get_request_id() is None


def test_async_post_queue_unavailable_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from fastapi_app.app import routers

    def fake_enqueue_submission_async(**kwargs):
        raise QueueUnavailable(
            "Queue is temporarily unavailable. Please try again shortly."
        )

    monkeypatch.setattr(
        routers, "enqueue_submission_async", fake_enqueue_submission_async
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
        "Queue is temporarily unavailable. Please try again shortly." in response.text
    )
    assert _count_submissions() == 0


def test_sync_form_rate_limit_returns_429(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from fastapi_app.app import routers

    def fake_rate_limit(**kwargs):
        raise RateLimitExceeded(retry_after=15)

    monkeypatch.setattr(routers, "enforce_rate_limit", fake_rate_limit)

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
    assert "Too many requests." in response.text
    assert response.headers["retry-after"] == "15"
    assert _count_submissions() == 0


def test_rate_limit_prefers_x_real_ip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from fastapi_app.app import routers

    captured: dict[str, str] = {}

    def fake_rate_limit(**kwargs):
        captured.update(kwargs)
        raise RateLimitExceeded(retry_after=5)

    monkeypatch.setattr(routers, "enforce_rate_limit", fake_rate_limit)

    response = client.post(
        "/sync-form",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "honeypot": "",
        },
        headers={
            "x-real-ip": "203.0.113.10",
            "x-forwarded-for": "198.51.100.1, 198.51.100.2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 429
    assert captured["identifier"] == "203.0.113.10"
    assert _count_submissions() == 0


def test_async_form_rate_limit_returns_429(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from fastapi_app.app import routers

    def fake_rate_limit(**kwargs):
        raise RateLimitExceeded(retry_after=20)

    monkeypatch.setattr(routers, "enforce_rate_limit", fake_rate_limit)

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
    assert "Too many requests." in response.text
    assert response.headers["retry-after"] == "20"
    assert _count_submissions() == 0


def test_honeypot_async_post_rejected(client: TestClient):
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
    assert "Spam detected." in response.text
    assert _count_submissions() == 0


def test_submissions_page_renders(client: TestClient):
    response = client.get("/submissions")
    assert response.status_code == 200
    assert "Saved Submissions" in response.text


def test_submissions_page_uses_configured_limit(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'fastapi_submissions_limit.db').as_posix()}"
    )
    app.state.submissions_page_limit = 7
    client = TestClient(app)

    from fastapi_app.app import routers

    captured: dict[str, int] = {}

    def fake_list_submissions(*, limit: int):
        captured["limit"] = limit
        return []

    monkeypatch.setattr(routers, "list_submissions", fake_list_submissions)

    response = client.get("/submissions")
    assert response.status_code == 200
    assert captured["limit"] == 7


def test_submissions_page_disabled_in_production(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "prod-test-secret")
    monkeypatch.setenv("ENABLE_SUBMISSIONS_PAGE", "false")
    app = create_app(
        database_url=f"sqlite:///{(tmp_path / 'prod_disabled_fastapi.db').as_posix()}"
    )
    client = TestClient(app)

    response = client.get("/submissions")
    assert response.status_code == 404


def test_production_requires_secret_key(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("ENABLE_SUBMISSIONS_PAGE", raising=False)

    with pytest.raises(RuntimeError, match="SECRET_KEY must be explicitly set"):
        create_app(
            database_url=f"sqlite:///{(tmp_path / 'prod_missing_secret_fastapi.db').as_posix()}"
        )
