# whitefly-python-task

## Project Overview

Backend recruitment assignment implemented with Flask and FastAPI parity, shared data/service layers, async processing, reverse proxy routing, load testing, anti-bot protections, and production-style reliability patterns.

The project now works as a compact backend reliability lab: one simple submission workflow, hardened through observability, migrations, idempotency, bounded retry, duplicate-safe redelivery, durable task status, read-only status APIs, and minimal UI polling.

## Architecture

- Public entrypoint: Nginx on `localhost:8080`
- App services: Flask + FastAPI
- Async processing: Celery worker
- Broker: Redis
- Database: PostgreSQL in Docker Compose, SQLite fallback for local/simple runs
- Shared domain layer: SQLAlchemy models, validation, service logic, idempotency, task status

Routing behind Nginx:
- `http://localhost:8080/flask/...` -> Flask service
- `http://localhost:8080/fastapi/...` -> FastAPI service

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Reliability design](docs/RELIABILITY.md)
- [Demo script](docs/DEMO.md)
- [Limitations and playbook](docs/LIMITATIONS.md)
- [Interview notes](docs/INTERVIEW_NOTES.md)

## Tech Stack

- Python 3.11
- Flask, FastAPI, Jinja2
- SQLAlchemy
- Celery + Redis
- PostgreSQL / SQLite
- Nginx
- Docker Compose
- Pytest
- k6

## Features

- Flask sync form: validate then persist directly.
- Flask async form: validate then enqueue Celery task.
- FastAPI sync form: same behavior as Flask sync.
- FastAPI async form: same behavior as Flask async.
- Shared service and validation layer used by both frameworks.
- Shared worker persistence path (`form_type="async"`, `status="processed"`).
- Redis-backed rate limiter with memory fallback.
- Honeypot and server-side validation hardening.
- Async task status JSON endpoints and browser polling.

## Reliability Summary

The reliability work is documented in detail in [Architecture](docs/ARCHITECTURE.md) and [Reliability design](docs/RELIABILITY.md).

Highlights:
- Safe `X-Request-ID` propagation across nginx, Flask, FastAPI, and Celery.
- JSON structured logs with PII-safe fields.
- Alembic baseline migrations for schema evolution.
- HTTP and service-layer idempotency.
- Bounded retry for transient SQLAlchemy `OperationalError`.
- Duplicate-safe `acks_late=True` and `reject_on_worker_lost=True`.
- Durable `task_statuses` table for async lifecycle visibility.
- Read-only task status APIs and minimal vanilla-JS polling on async forms.

## PR Timeline

| PR | Capability added |
| --- | --- |
| #1 | Request IDs and JSON structured logging across nginx, Flask, FastAPI, and worker |
| #2 | FastAPI unhandled 500 responses preserve safe `X-Request-ID` |
| #3 | Worker failure visibility with task id, retries, task name, and error type |
| #4 | SQLAlchemy `hide_parameters=True` and PII-safe logging regression coverage |
| #5 | Alembic baseline migrations |
| #6 | Nullable unique `Submission.idempotency_key` schema |
| #7 | Service-layer idempotent persistence |
| #8 | HTTP `Idempotency-Key` contract and async idempotency propagation |
| #9 | Bounded retry for transient SQLAlchemy `OperationalError` |
| #10 | Duplicate-safe late ack and worker-lost redelivery |
| #11 | Durable async task status recording in `task_statuses` |
| #12 | Read-only JSON async task status endpoints |
| #13 | Minimal async status UI polling |

## Running Locally

Install dependencies and run tests:

```powershell
.\.venv\Scripts\python -m pip install -r requirements\dev.txt
.\.venv\Scripts\python -m pytest -q
```

Run Flask:

```powershell
.\.venv\Scripts\python -m flask --app flask_app.app run --debug
```

Run FastAPI:

```powershell
.\.venv\Scripts\python -m uvicorn fastapi_app.app.main:app --reload --port 8000
```

## Database Migrations

Alembic is configured under `migrations/`. For a fresh database, run migrations before first app startup:

```powershell
.\.venv\Scripts\alembic upgrade head
```

The baseline matches the schema that the app can create with SQLAlchemy metadata. If the app already created the current schema with `create_all`, mark the database as current before future migrations:

```powershell
docker compose exec flask_app alembic stamp head
```

Alembic is not auto-run.

## Running With Docker Compose

```powershell
Copy-Item .env.example .env -Force
docker compose up -d --build
docker compose ps
```

Main URLs:
- Flask: `http://localhost:8080/flask/`
- FastAPI: `http://localhost:8080/fastapi/`

Useful logs:

```powershell
docker compose logs -f
docker compose logs -f worker
docker compose logs -f nginx
```

Health check status:

```powershell
docker inspect --format "{{json .State.Health.Status}}" whitefly-flask
docker inspect --format "{{json .State.Health.Status}}" whitefly-fastapi
```

Stop:

```powershell
docker compose down
```

## Request IDs And Logs

Flask, FastAPI, nginx, and the Celery worker write JSON logs with a `request_id` field. The apps echo `X-Request-ID` on every response; if a caller omits it, the edge/app generates one.

Example trace with a caller-supplied ID:

```powershell
curl.exe -i -H "X-Request-ID: demo-trace-001" -d "first_name=Grace&last_name=Hopper&email=grace@example.com&honeypot=" http://localhost:8080/flask/async-form
docker compose logs nginx flask_app worker | Select-String demo-trace-001
```

## Async Status

Async task lifecycle is recorded in the `task_statuses` table with these states:
- `queued`
- `processing`
- `retrying`
- `processed`
- `failed`
- `conflict`

Read-only JSON status endpoints:
- `GET /flask/async-status/<task_id>`
- `GET /fastapi/async-status/{task_id}`

Async form pages poll the status endpoint every two seconds after enqueue, stop on terminal states, and cap after about 60 seconds. Status responses and the polling UI do not expose names, emails, or form payloads.

## Testing

Full suite:

```powershell
.\.venv\Scripts\python -m pytest -q
```

The pytest suite currently passes with 140+ tests.

Coverage includes:
- Flask/FastAPI route behavior
- request IDs and structured logging
- idempotency and conflict behavior
- migration parity
- worker retry/redelivery behavior
- async task status recording and read endpoints
- rate-limit fallback behavior
- trusted client IP extraction behavior

## Load Testing

Run locally if `k6` is installed:

```powershell
k6 run load_tests/sync_form.js
k6 run load_tests/async_form.js
```

Docker fallback:

```powershell
docker run --rm --network whitefly-python-task_default -e BASE_URL=http://nginx/flask -v ${PWD}/load_tests:/scripts grafana/k6 run /scripts/sync_form.js
docker run --rm --network whitefly-python-task_default -e BASE_URL=http://nginx/fastapi -v ${PWD}/load_tests:/scripts grafana/k6 run /scripts/async_form.js
```

Latest sample summary:
- Sync form: 0% failed requests, p95 `24.38ms`
- Async form: 0% failed requests, p95 `8.76ms`
- Both scripts met thresholds (`http_req_failed < 5%`, `p(95) < 1000ms`)
- Async endpoint is faster because it validates and queues work immediately; persistence runs later in the Celery worker.

## Security And Reliability Improvements

- Production secret handling:
  - `APP_ENV=production` requires explicit `SECRET_KEY`; startup fails fast if missing.
- Production submissions-page restriction:
  - `/submissions` is disabled by default in production (`ENABLE_SUBMISSIONS_PAGE=false`).
- Async broker failure behavior:
  - queue publish failures are converted to controlled `503` responses with user-friendly feedback.
- Trusted proxy / client IP handling:
  - Nginx forwards normalized client IP headers.
  - App layer prefers `X-Real-IP` and uses a shared trusted-identifier helper for rate limiting.
- Redis-backed rate limiting with memory fallback:
  - primary storage is Redis.
  - if Redis is unavailable, limiter falls back to in-memory buckets and logs explicit warnings.
- Additional production-quality safeguards:
  - `pool_pre_ping=True` for non-SQLite database engines.
  - submissions list queries are bounded via configurable `SUBMISSIONS_PAGE_LIMIT` (default `200`).
  - honeypot is validated before deeper field validation.

## Future Improvements

- Add a real dead-letter/failure queue and triage workflow.
- Add auth for internal status and submissions surfaces.
- Add metrics and tracing backend integration.
- Add CSP/static-JS setup instead of inline polling JavaScript.
- Add GET rate limiting for status polling.
- Harden production deployment and operational runbooks.
- Expand fingerprint-based anti-bot signals.
