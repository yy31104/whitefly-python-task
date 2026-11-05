"""Span structure for one async submission, from the HTTP request to the worker.

These tests send a real Celery message through the in-memory broker and run the
task in an in-thread worker, so the trace context crosses the broker in the
message headers exactly as it does with Redis. Spans go to an in-memory exporter.
"""

from __future__ import annotations

import time

import pytest
from celery.contrib.testing.worker import start_worker
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind

import worker.tasks as worker_tasks
from fastapi_app.app.main import create_app as create_fastapi_app
from flask_app.app import create_app as create_flask_app
from shared.task_status import get_task_status
from worker.celery_app import celery_app

TASK = "worker.save_async_submission"
PII = ("Ada", "Lovelace", "ada@example.com")
_EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="session")
def _tracer_provider():
    # The global provider can be set once per process; every test shares it.
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(provider)
    return provider


@pytest.fixture
def spans(_tracer_provider):
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


@pytest.fixture
def memory_worker(monkeypatch):
    """Point the real Celery app at the in-memory broker and run a worker thread."""
    previous = {
        key: celery_app.conf.get(key)
        for key in ("broker_url", "result_backend", "task_ignore_result")
    }
    celery_app.conf.update(broker_url="memory://", result_backend="cache+memory://")
    celery_app._pool = None
    monkeypatch.setattr(worker_tasks, "_db_ready", True)
    with start_worker(
        celery_app, pool="solo", perform_ping_check=False, shutdown_timeout=10
    ):
        yield
    celery_app.conf.update(**previous)
    celery_app._pool = None


def _wait_for_span(exporter, name: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for span in exporter.get_finished_spans():
            if span.name == name:
                return span
        time.sleep(0.05)
    raise AssertionError(f"span {name!r} was never exported")


def _by_id(spans_list):
    return {span.context.span_id: span for span in spans_list}


def _ancestors(span, index):
    chain = []
    parent = span.parent
    while parent is not None and parent.span_id in index:
        chain.append(index[parent.span_id])
        parent = index[parent.span_id].parent
    return chain


def _assert_one_trace_from_http_to_worker(exporter, server_span_name: str):
    run_span = _wait_for_span(exporter, f"run/{TASK}")
    finished = exporter.get_finished_spans()
    index = _by_id(finished)

    server = [s for s in finished if s.kind is SpanKind.SERVER]
    assert [s.name for s in server] == [server_span_name]
    server_span = server[0]
    assert server_span.parent is None
    trace_id = server_span.context.trace_id

    publish = [s for s in finished if s.name == f"apply_async/{TASK}"]
    assert len(publish) == 1
    publish_span = publish[0]
    assert publish_span.kind is SpanKind.PRODUCER
    assert publish_span.context.trace_id == trace_id
    assert server_span in _ancestors(publish_span, index)

    # The worker span crossed the broker: its parent is the publish span.
    assert run_span.kind is SpanKind.CONSUMER
    assert run_span.context.trace_id == trace_id
    assert run_span.parent.span_id == publish_span.context.span_id
    assert run_span.attributes["celery.action"] == "run"
    assert run_span.attributes["celery.state"] == "SUCCESS"

    db_spans = [
        s for s in finished if s.kind is SpanKind.CLIENT and "db.system" in s.attributes
    ]
    request_db = [s for s in db_spans if server_span in _ancestors(s, index)]
    worker_db = [s for s in db_spans if run_span in _ancestors(s, index)]
    assert request_db, "the request's claim/status writes must be traced"
    assert worker_db, "the worker's writes must be traced"
    assert all(s.context.trace_id == trace_id for s in request_db + worker_db)
    assert any(
        "INSERT INTO submissions" in s.attributes["db.statement"] for s in worker_db
    )
    assert any(
        "INSERT INTO idempotency_claims" in s.attributes["db.statement"]
        for s in request_db
    )

    # Only one trace was produced for the whole submission.
    assert {s.context.trace_id for s in finished} == {trace_id}

    # No form field ends up in any attribute.
    rendered = repr([dict(s.attributes) for s in finished])
    for value in PII:
        assert value not in rendered
    return server_span, publish_span, run_span


FORM = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "idempotency_key": "trace-key-1",
}


def test_flask_async_submission_is_one_trace(tmp_path, spans, memory_worker):
    app = create_flask_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": f"sqlite:///{(tmp_path / 'flask.db').as_posix()}",
        }
    )
    spans.clear()  # drop the startup create_all() spans
    response = app.test_client().post("/async-form", data=FORM)
    assert response.status_code == 302
    task_id = response.headers["Location"].split("task_id=")[1]
    _, publish_span, run_span = _assert_one_trace_from_http_to_worker(
        spans, "POST /async-form"
    )
    assert get_task_status(task_id).status == "processed"
    assert publish_span.attributes["messaging.message.id"] == task_id
    assert run_span.attributes["messaging.message.id"] == task_id


def test_fastapi_async_submission_is_one_trace(tmp_path, spans, memory_worker):
    app = create_fastapi_app(
        database_url=f"sqlite:///{(tmp_path / 'fastapi.db').as_posix()}"
    )
    with TestClient(app) as client:
        spans.clear()  # drop the startup create_all() spans
        response = client.post("/async-form", data=FORM, follow_redirects=False)
    assert response.status_code == 303
    task_id = response.headers["location"].split("task_id=")[1]
    _assert_one_trace_from_http_to_worker(spans, "POST /async-form")
    assert get_task_status(task_id).status == "processed"


def test_incoming_traceparent_is_continued(tmp_path, spans):
    app = create_flask_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": f"sqlite:///{(tmp_path / 'tp.db').as_posix()}",
        }
    )
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    app.test_client().get(
        "/sync-form",
        headers={"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"},
    )
    (server_span,) = [
        s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER
    ]
    assert format(server_span.context.trace_id, "032x") == trace_id
    assert format(server_span.parent.span_id, "016x") == "00f067aa0ba902b7"


def test_healthz_is_not_traced(tmp_path, spans):
    app = create_fastapi_app(
        database_url=f"sqlite:///{(tmp_path / 'health.db').as_posix()}"
    )
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
    assert [s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER] == []
