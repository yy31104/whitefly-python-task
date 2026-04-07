from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fastapi_app.app.main import create_app
from shared.db import session_scope
from shared.models import Submission
from shared.rate_limit import RateLimitExceeded
from shared.services import QueueUnavailable


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


def test_home_page(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "Hello World" in response.text


def test_sync_form_get(client: TestClient):
    response = client.get("/sync-form")
    assert response.status_code == 200
    assert "Sync Submission Form" in response.text


def test_async_form_get(client: TestClient):
    response = client.get("/async-form")
    assert response.status_code == 200
    assert "Async Submission Form" in response.text


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


def test_valid_async_post_queues_task(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, str]] = []

    class DummyTaskResult:
        id = "task-fastapi-123"

    def fake_delay(*, first_name: str, last_name: str, email: str):
        calls.append({"first_name": first_name, "last_name": last_name, "email": email})
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
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Submission queued. Task ID: task-fastapi-123" in response.text
    assert calls == [
        {
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
        }
    ]
    assert _count_submissions() == 0


def test_async_post_queue_unavailable_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from fastapi_app.app import routers

    def fake_enqueue_submission_async(**kwargs):
        raise QueueUnavailable("Queue is temporarily unavailable. Please try again shortly.")

    monkeypatch.setattr(routers, "enqueue_submission_async", fake_enqueue_submission_async)

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
    assert "Queue is temporarily unavailable. Please try again shortly." in response.text
    assert _count_submissions() == 0


def test_sync_form_rate_limit_returns_429(client: TestClient, monkeypatch: pytest.MonkeyPatch):
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


def test_async_form_rate_limit_returns_429(client: TestClient, monkeypatch: pytest.MonkeyPatch):
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


def test_submissions_page_disabled_in_production(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "prod-test-secret")
    monkeypatch.setenv("ENABLE_SUBMISSIONS_PAGE", "false")
    app = create_app(database_url=f"sqlite:///{(tmp_path / 'prod_disabled_fastapi.db').as_posix()}")
    client = TestClient(app)

    response = client.get("/submissions")
    assert response.status_code == 404


def test_production_requires_secret_key(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("ENABLE_SUBMISSIONS_PAGE", raising=False)

    with pytest.raises(RuntimeError, match="SECRET_KEY must be explicitly set"):
        create_app(database_url=f"sqlite:///{(tmp_path / 'prod_missing_secret_fastapi.db').as_posix()}")
