# Form submission service

![Python](https://img.shields.io/badge/Python-3.11%2B-286B9C?style=flat-square&labelColor=25292E)
![Queue](https://img.shields.io/badge/Queue-Celery-286B9C?style=flat-square&labelColor=25292E)

Accepting a form and saving it are different events.

This service accepts a name and email through the same form workflow in Flask and FastAPI. Synchronous requests save before returning; asynchronous requests return a task ID while a Celery worker saves the submission. Both paths share validation and idempotent persistence; the async path also records task status.

Take-home assignment, extended as a personal project. Built with AI assistance.

[Run locally](#run-locally) · [Docs](#docs)

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

## How it works

1. [nginx.conf](nginx/nginx.conf) routes `/flask/` and `/fastapi/` and forwards a validated request ID.
2. [Flask routes](flask_app/app/routes.py) and [FastAPI routes](fastapi_app/app/routers.py) pass form fields through the shared [validation](shared/validation.py).
3. [services.py](shared/services.py) writes synchronous submissions or publishes asynchronous work to Celery.
4. [tasks.py](worker/tasks.py) retries transient database failures and persists through the same service layer.
5. [task_status.py](shared/task_status.py) records lifecycle state; the read-only status routes return it to the polling form.

The same idempotency key and payload reuse one row; a changed payload is a conflict. Worker redelivery without a client key reuses the Celery task ID.

## Project layout

| Path | What | Start here |
| --- | --- | --- |
| `flask_app/`, `fastapi_app/` | HTTP routes and forms | [Flask](flask_app/app/routes.py) · [FastAPI](fastapi_app/app/routers.py) |
| `shared/` | Validation, persistence, logging, and task state | [services.py](shared/services.py) |
| `worker/` | Celery configuration and persistence task | [tasks.py](worker/tasks.py) |
| `migrations/` | Alembic schema revisions | [env.py](migrations/env.py) |
| `tests/` | Local SQLite and mocked-service checks | [Worker behavior](tests/worker/test_tasks.py) |
| `docker/` | Service images built with the repository root as context | [Flask image](docker/Dockerfile.flask) |

## Run locally

Requires Python 3.12 for local checks and Docker Compose for the full stack. Start with a fresh clone; migrate SQLite before importing the apps or running tests.

```bash
git clone https://github.com/yy31104/whitefly-python-task.git
```

```bash
cd whitefly-python-task
```

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/python -m pip install -r requirements/dev.txt
```

```bash
DATABASE_URL=sqlite:///app.db .venv/bin/alembic upgrade head
```

Local checks take a few seconds after installation:

```bash
.venv/bin/python -m pytest -q
```

```bash
.venv/bin/ruff check .
```

```bash
.venv/bin/black --check .
```

For the full stack, copy the local configuration, build the images, and migrate PostgreSQL before starting the app containers:

```bash
cp .env.example .env
```

```bash
docker compose build
```

```bash
docker compose run --rm flask_app alembic upgrade head
```

```bash
docker compose up -d
```

Open [Flask](http://localhost:8080/flask/) or [FastAPI](http://localhost:8080/fastapi/). The [demo guide](docs/DEMO.md) covers submissions, duplicate keys, and status polling.

```bash
docker compose down
```

## Design notes

Both HTTP frameworks use one service layer, so validation and persistence rules have one implementation. Their route adapters still exercise each framework's request and response handling.

Task states have their own table, so queued and failed work can be queried before a submission row exists. Celery result storage stays disabled; PostgreSQL holds the user-visible status.

SQLite keeps unit checks local. Compose exercises the same workflow with PostgreSQL, Redis, Nginx, and a separate worker.

## Limits

- The demo has no authentication, and Compose publishes port 8080 on all host interfaces. Use generated data in a trusted local environment.
- Status writes are best effort: a lookup can be missing or stale while the primary action succeeds. Browser polling stops after about a minute.
- Retries are bounded and there is no dead-letter queue. The default limiter accepts 20 POSTs per minute per client and endpoint; load tests need rate limits matched to their request rate.

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Reliability mechanisms](docs/RELIABILITY.md)
- [Demo and request examples](docs/DEMO.md)
- [Operational limits and recovery](docs/LIMITATIONS.md)
