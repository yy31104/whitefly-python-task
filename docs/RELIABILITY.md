# Reliability Design

Each mechanism below is framed as Problem -> Mechanism -> Guarantee -> Code pointers.

## X-Request-ID And JSON Logging

Problem: Without a correlation id, one user action cannot be followed across nginx, the app, and the worker.

Mechanism: Nginx validates or stamps `X-Request-ID`. Flask and FastAPI bind it to a context variable and echo it on responses. The worker receives the request id through Celery kwargs. Python logging emits JSON lines with `request_id` when available.

Guarantee: A single request can be traced across nginx access logs, app logs, and worker logs without logging form payloads.

Code pointers:
- `shared/logging.py`
- `flask_app/app/routes.py`
- `fastapi_app/app/routers.py`
- `worker/tasks.py`
- `tests/`

## FastAPI 500 X-Request-ID Behavior

Problem: Unhandled FastAPI exceptions can bypass normal response construction and lose the request id header.

Mechanism: FastAPI registers an exception handler that returns a generic 500 response with the bound or generated `X-Request-ID`.

Guarantee: Even unhandled 500 responses include a safe request id, and the request-id context is cleared after the error.

Code pointers:
- `fastapi_app/app/routers.py`
- `shared/logging.py`
- `tests/`

## SQLAlchemy Parameter Hiding And PII-Safe Logging

Problem: SQL errors and logs can accidentally expose names, emails, secrets, database URLs, or broker URLs.

Mechanism: SQLAlchemy engines are created with hidden parameters, and app/worker logs use structured safe fields rather than raw form payloads.

Guarantee: Operational logs and SQL exception text are much less likely to leak PII or secrets.

Code pointers:
- `shared/db.py`
- `shared/models.py`
- `shared/services.py`
- `worker/tasks.py`
- `tests/`

## Idempotency

Problem: Retries, double submits, browser refreshes, and worker redelivery can create duplicate submissions.

Mechanism: The schema adds nullable unique `Submission.idempotency_key`. The service layer centralizes deduplication. HTTP routes accept an `Idempotency-Key` header and hidden browser form keys. The worker uses `effective_idempotency_key = client idempotency_key or Celery task_id`.

Guarantee: Same key plus same payload returns the existing row. Same key plus different payload becomes an `IdempotencyConflict`. Keyless async retries and redeliveries converge on the Celery `task_id`.

Code pointers:
- `shared/idempotency.py`
- `shared/services.py`
- `shared/models.py`
- `worker/tasks.py`
- `flask_app/app/routes.py`
- `fastapi_app/app/routers.py`
- `migrations/versions/`
- `tests/`

## Bounded OperationalError Retry

Problem: A transient database outage should not immediately drop an async submission.

Mechanism: The Celery task autoretries only SQLAlchemy `OperationalError`, with max retries 3, exponential backoff, and jitter.

Guarantee: Transient DB failures get bounded retry without retrying deterministic validation, idempotency, integrity, or generic application errors.

Code pointers:
- `worker/tasks.py`
- `worker/celery_app.py`
- `tests/`

## Late Ack And Worker-Lost Redelivery

Problem: If a worker dies after receiving a task, the broker can otherwise consider the message handled too early.

Mechanism: The task uses `acks_late=True` and `reject_on_worker_lost=True`. Duplicate safety comes from the effective idempotency key, not from optimistic assumptions about delivery.

Guarantee: If a worker is killed after DB commit but before ack, broker redelivery reruns the same task id and reuses the existing submission instead of inserting a duplicate.

Code pointers:
- `worker/tasks.py`
- `worker/celery_app.py`
- `shared/services.py`
- `tests/`

## Durable Task Status Recording

Problem: Async failures may happen before a submission row exists, so there needs to be visibility independent of `Submission.status`.

Mechanism: `task_statuses` stores current lifecycle state by Celery `task_id`. Status writes are best-effort and never allowed to break enqueue or worker processing.

Guarantee: The app can answer "what happened to task X?" for queued, processing, retrying, processed, failed, and conflict states while keeping Celery result backend ignored.

Code pointers:
- `shared/task_status.py`
- `shared/models.py`
- `shared/services.py`
- `worker/tasks.py`
- `migrations/versions/`
- `tests/`

## Async Status API And UI Polling

Problem: After async enqueue, the browser needs feedback beyond a static "queued" message.

Mechanism: Flask and FastAPI expose read-only JSON task-status endpoints. The async form pages render a sanitized task id and inline vanilla JavaScript polls the status URL every two seconds until a terminal state or cap.

Guarantee: Users can see queued, processing, retrying, processed, failed, or conflict status without exposing names, emails, or form payloads.

Code pointers:
- `shared/task_status.py`
- `flask_app/app/routes.py`
- `fastapi_app/app/routers.py`
- `tests/`

## Failure Modes

| Failure mode | Behavior | Why it is safe or visible | Code pointers |
| --- | --- | --- | --- |
| Crash before commit | Message can be redelivered because late ack is enabled | Same task id is reused; no submission was committed yet | `worker/tasks.py`, `shared/services.py` |
| Crash after commit before ack | Message can be redelivered | Same task id maps to the same effective idempotency key and reuses the row | `worker/tasks.py`, `shared/services.py` |
| DB down | Worker retries bounded `OperationalError`; exhausted retries become failed status/logs | Retry is bounded and visible | `worker/tasks.py`, `shared/task_status.py` |
| Redis down | Async publish failure becomes controlled queue-unavailable behavior | Request path does not pretend enqueue succeeded | `shared/services.py`, `flask_app/app/routes.py`, `fastapi_app/app/routers.py` |
| Duplicate key | Same key and same payload reuses; different payload conflicts | Prevents duplicate rows while protecting semantic mismatch | `shared/services.py`, `shared/idempotency.py` |
| IdempotencyConflict | Sync returns 409; async records conflict and logs terminal failure | Deterministic conflict does not retry forever | `shared/services.py`, `worker/tasks.py`, `shared/task_status.py` |
