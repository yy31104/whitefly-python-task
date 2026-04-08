# whitefly-python-task

## Project Overview
Backend recruitment assignment implemented with Flask and FastAPI parity, shared data/service layers, async processing, reverse proxy routing, load testing, and practical anti-bot protections.

## Architecture
- Public entrypoint: Nginx on `localhost:8080`
- App services: Flask + FastAPI
- Async processing: Celery worker
- Broker: Redis
- Database: PostgreSQL (Compose runtime), SQLite fallback for local/simple runs
- Shared domain layer: SQLAlchemy models, validation, service logic

Routing behind Nginx:
- `http://localhost:8080/flask/...` -> Flask service
- `http://localhost:8080/fastapi/...` -> FastAPI service

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
- Shared Redis-backed rate limiter with `429 + Retry-After`.
- Honeypot and server-side validation hardening.

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

## Running with Docker Compose
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

Health check status (explicit container names used in this project):
```powershell
docker inspect --format "{{json .State.Health.Status}}" whitefly-flask
docker inspect --format "{{json .State.Health.Status}}" whitefly-fastapi
```

Portable alternative (resolve IDs first):
```powershell
docker compose ps
docker inspect --format "{{json .State.Health.Status}}" (docker compose ps -q flask_app)
docker inspect --format "{{json .State.Health.Status}}" (docker compose ps -q fastapi_app)
```

Stop:
```powershell
docker compose down
```

## Testing
Full suite:
```powershell
.\.venv\Scripts\python -m pytest -q
```

Current test status: `49 passed`

Coverage includes:
- Flask/FastAPI route behavior (sync/async, validation, rate-limit, security toggles)
- Worker persistence path
- Shared validation edge cases
- Rate-limit fallback behavior
- Trusted client IP extraction behavior

## Load Testing
Run locally (if `k6` installed):
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

## Security and Reliability Improvements
- Production secret handling:
  - `APP_ENV=production` requires explicit `SECRET_KEY`; startup fails fast if missing.
- Production submissions-page restriction:
  - `/submissions` is disabled by default in production (`ENABLE_SUBMISSIONS_PAGE=false`).
- Async broker failure behavior:
  - queue publish failures are converted to controlled `503` responses with user-friendly feedback (not generic `500`).
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
- Database migrations:
  - introduce Alembic-based schema migration workflow.
- Stronger admin/auth protection:
  - protect internal pages (such as submissions) with robust authentication/authorization.
- Richer async lifecycle:
  - model and expose `queued -> processing -> processed/failed` job status transitions.
- Fingerprint-based anti-bot expansion:
  - add fingerprint signal (for example FingerprintJS) to complement IP-based controls.
