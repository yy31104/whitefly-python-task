from __future__ import annotations

import pytest

from flask_app.app import create_app
from shared.db import session_scope
from shared.models import Submission


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


def test_submissions_page_renders(client):
    response = client.get("/submissions")
    assert response.status_code == 200
    assert b"Saved Submissions" in response.data
