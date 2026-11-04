# Form submission service

Submit the same form through Flask or FastAPI, either synchronously or through a Celery worker. Both paths validate names and email addresses, save submissions, and reuse earlier writes when an idempotency key is replayed.

Take-home assignment, extended as a personal project. Built with AI assistance.

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

## Start the stack

Requires Docker Compose. Run from a fresh clone:

```bash
git clone https://github.com/yy31104/whitefly-python-task.git
cd whitefly-python-task
cp .env.example .env
docker compose build
docker compose run --rm flask_app alembic upgrade head
docker compose up -d
```

Open [Flask](http://localhost:8080/flask/) or [FastAPI](http://localhost:8080/fastapi/). Forms are public in this local demo, and Compose exposes port 8080; use generated data. The [demo guide](docs/DEMO.md) covers submissions, status polling, and duplicate keys.

Stop the services when finished; the database volume is retained:

```bash
docker compose down
```

## Check locally

With Python 3.12, these checks take a few seconds after installation. Migrate SQLite before importing the apps:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements/dev.txt
DATABASE_URL=sqlite:///app.db .venv/bin/alembic upgrade head
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/black --check .
```

## Shared persistence

The [Flask](flask_app/app/routes.py) and [FastAPI](fastapi_app/app/routers.py) adapters call one [service layer](shared/services.py), keeping validation and persistence rules together while preserving each framework's request handling.

[Task status](shared/task_status.py) has its own table so queued and failed work can be queried before a submission row exists. Status writes are best effort; PostgreSQL holds the user-visible state and Celery result storage stays disabled.

Read the [architecture](docs/ARCHITECTURE.md), [retry and idempotency behavior](docs/RELIABILITY.md), or [operational limits](docs/LIMITATIONS.md) for the rest of the workflow.
