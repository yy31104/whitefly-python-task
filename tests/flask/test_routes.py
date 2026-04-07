from __future__ import annotations

import pytest

from flask_app.app import create_app
from shared.db import session_scope
from shared.models import Submission
from shared.rate_limit import RateLimitExceeded
from shared.services import QueueUnavailable


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


def test_home_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Hello World" in response.data


def test_sync_form_get(client):
    response = client.get("/sync-form")
    assert response.status_code == 200
    assert b"Sync Submission Form" in response.data


def test_async_form_get(client):
    response = client.get("/async-form")
    assert response.status_code == 200
    assert b"Async Submission Form" in response.data


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


def test_valid_async_post_queues_task(client, monkeypatch):
    calls: list[dict[str, str]] = []

    class DummyTaskResult:
        id = "task-123"

    def fake_delay(*, first_name: str, last_name: str, email: str):
        calls.append(
            {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
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
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Submission queued. Task ID: task-123" in response.data
    assert calls == [
        {
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "grace@example.com",
        }
    ]
    assert _count_submissions() == 0


def test_async_post_queue_unavailable_returns_503(client, monkeypatch):
    from flask_app.app import routes

    def fake_enqueue_submission_async(**kwargs):
        raise QueueUnavailable("Queue is temporarily unavailable. Please try again shortly.")

    monkeypatch.setattr(routes, "enqueue_submission_async", fake_enqueue_submission_async)

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
    assert b"Queue is temporarily unavailable. Please try again shortly." in response.data
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
