# Form submission service

A double-clicked submit button or a redelivered queue message can save the same form twice.

[![CI](https://img.shields.io/github/actions/workflow/status/yy31104/whitefly-python-task/tests.yml?branch=main&style=flat-square&labelColor=25292E)](https://github.com/yy31104/whitefly-python-task/actions/workflows/tests.yml?query=branch%3Amain)
![Python](https://img.shields.io/badge/Python-3.11%2B-3572A5?style=flat-square&labelColor=25292E)
![Flask](https://img.shields.io/badge/API-Flask%20%2B%20FastAPI-AD7F2B?style=flat-square&labelColor=25292E)

Submit the same form through Flask or FastAPI, either synchronously or through a Celery worker. Both paths validate names and email addresses, save submissions, and reuse earlier writes when an idempotency key is replayed.

Take-home assignment, extended as a personal project. Built with AI assistance.

## How it works

```text
browser ─► Nginx :8080 ─┬─► /flask/   Flask (gunicorn) ──┐
                        └─► /fastapi/ FastAPI (uvicorn) ─┤
                                                         ▼
                              shared/services.py: validate, claim key, persist
                                    │ sync                         │ async
                                    ▼                              ▼
                               PostgreSQL ◄──── Celery worker ◄── Redis
                                    │
                                    └── task_statuses: queued → processing → processed | failed
```

1. **Validate.** Flask and FastAPI routes are thin adapters over one [service layer](shared/services.py), so validation, idempotency and persistence behave the same in both.
2. **Claim the key.** The client key comes from the `Idempotency-Key` header or a hidden form field. Async requests insert it into `idempotency_claims` before enqueueing, so of two concurrent first requests exactly one enqueues.
3. **Classify reuse.**
   - Same key and same payload: replay, returning the original result.
   - Same key and different payload: **422**.
   - Same key while the original is still queued or running: **409**.
4. **Persist.** Sync requests write in the request. Async requests are written by the [worker](worker/tasks.py), which retries transient database errors with bounded backoff and uses `acks_late`. Redelivered messages converge on one row through the key, or through the Celery task id when there is no key.
5. **Report status.** [Task status](shared/task_status.py) moves `queued → processing → processed | failed` and never leaves a terminal state. The browser polls `/async-status/<task_id>`.

Each request carries an `X-Request-ID` through the JSON logs and into the worker. SQL parameters are hidden from logs.

## Project layout

```text
flask_app/      Flask routes, forms and templates
fastapi_app/    FastAPI routes and templates
shared/         services, idempotency, task status, validation, logging, rate limit
worker/         Celery app and the save task
migrations/     Alembic revisions
nginx/          reverse-proxy config
tests/          pytest suites per framework, shared modules and worker
docs/           architecture, reliability, limits, demo
```

## Run locally

Requires Docker Compose. Run from a fresh clone:

```bash
git clone https://github.com/yy31104/whitefly-python-task.git
cd whitefly-python-task
cp .env.example .env
docker compose build
docker compose run --rm flask_app alembic upgrade head
docker compose up -d
```

Open [Flask](http://localhost:8080/flask/) or [FastAPI](http://localhost:8080/fastapi/). Forms are public in this local demo, and Compose exposes port 8080; use generated data. The [demo guide](docs/DEMO.md) covers submissions, status polling, and duplicate keys. Stop with `docker compose down`; the database volume is retained.

A real local Compose run with generated form data:

```text
flask health: 200
flask sync: 302 request_id=demo-flask-sync
flask async: processed; status payload contains no form fields
flask honeypot: 400
fastapi health: 200
fastapi sync: 303 request_id=demo-fastapi-sync
fastapi async: processed; status payload contains no form fields
fastapi honeypot: 400
idempotent sync replays: one stored row per key
```

Tests and lint, with Python 3.12 (migrate SQLite before importing the apps):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements/dev.txt
DATABASE_URL=sqlite:///app.db .venv/bin/alembic upgrade head
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/black --check .
```

CI runs the same three checks on every push and pull request ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)).

## Design notes

- **Both frameworks share one service layer.** Running Flask and FastAPI side by side shows the reliability rules live in `shared/`, not in either framework's request handling.
- **Task status has its own table.** An async submission row only exists after the worker succeeds. A separate `task_statuses` table lets queued and failed work be queried before then. Celery result storage stays disabled, so PostgreSQL holds the user-visible state.
- **Claim before enqueue.** The database primary key on `idempotency_claims` is the atomic "first request wins" step. The broker never sees a duplicate first request.
- **Status codes follow `draft-ietf-httpapi-idempotency-key-header`.** It uses 422 for a key reused with a different payload and 409 while the original is outstanding. The draft is not an RFC.

## Limits

- No authentication. The forms, the status endpoint and the submissions page are open; this is a local demo.
- Idempotency keys are global, not scoped per client, and never expire.
- No dead-letter queue. A task that exhausts its retries is recorded as `failed` and stays there.
- Task status writes are best effort. A status row can lag the submission, or be missing if the database is unavailable at that moment.
- No metrics or tracing backend. Correlation is by `X-Request-ID` in the JSON logs only.
- Migrations are not run on startup.
- The test suite uses SQLite and a patched Celery `apply_async`. PostgreSQL and Redis are exercised only by the Compose run above.
- The async status UI uses inline JavaScript, and there is no JavaScript test harness.

## Docs

- [Architecture](docs/ARCHITECTURE.md): topology, sync and async flows, state machine, data model
- [Reliability](docs/RELIABILITY.md): request ids, idempotency, retries, redelivery, task status
- [Limitations](docs/LIMITATIONS.md): operational limits and rollback playbook
- [Demo](docs/DEMO.md): a scripted walk through the running stack
