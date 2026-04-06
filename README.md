# whitefly-python-task

Python backend recruitment assignment project for Whitefly.

## Project status
All milestones are completed.

- M0: project skeleton
- M1: Flask sync flow
- M2: Flask async flow with Celery + Redis
- M3: FastAPI equivalent flow
- M4: Docker Compose + Nginx + PostgreSQL + k6
- M5: anti-bot protections (honeypot + validation hardening + rate limiting)

## Architecture
- Nginx as a single public entrypoint (`localhost:8080`)
- Flask app service
- FastAPI app service
- Celery worker service
- Redis (broker/result backend)
- PostgreSQL (shared database)

Nginx routing:
- `http://localhost:8080/flask/...` -> Flask
- `http://localhost:8080/fastapi/...` -> FastAPI

## Features implemented
- Flask sync form: validate and save directly to DB
- Flask async form: validate and enqueue Celery task
- FastAPI sync form: validate and save directly to DB
- FastAPI async form: validate and enqueue Celery task
- Shared SQLAlchemy model/service/validation layer
- Shared async worker flow (Redis + Celery)
- Reverse proxy routing through Nginx
- Load testing scripts with k6
- Anti-bot protections for both frameworks

## Routes
Flask (through Nginx):
- `GET /flask/`
- `GET /flask/sync-form`
- `POST /flask/sync-form`
- `GET /flask/async-form`
- `POST /flask/async-form`
- `GET /flask/submissions`

FastAPI (through Nginx):
- `GET /fastapi/`
- `GET /fastapi/sync-form`
- `POST /fastapi/sync-form`
- `GET /fastapi/async-form`
- `POST /fastapi/async-form`
- `GET /fastapi/submissions`

## Local development (without Docker)
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

## Docker Compose run (recommended)
```powershell
Copy-Item .env.example .env -Force
docker compose up -d --build
docker compose ps
```

Main URLs:
- Flask: `http://localhost:8080/flask/`
- FastAPI: `http://localhost:8080/fastapi/`

Inspect logs:
```powershell
docker compose logs -f
docker compose logs -f worker
docker compose logs -f nginx
```

Stop stack:
```powershell
docker compose down
```

## Production security defaults
- Set `APP_ENV=production` for production runtime behavior.
- When `APP_ENV=production`, `SECRET_KEY` must be explicitly provided or app startup fails.
- In production mode, `/submissions` is disabled by default (`ENABLE_SUBMISSIONS_PAGE=false`) to avoid public PII exposure.
- You can override this behavior explicitly with `ENABLE_SUBMISSIONS_PAGE=true` if needed behind trusted protection.

## Load testing (k6)
Local k6:
```powershell
k6 run load_tests/sync_form.js
k6 run load_tests/async_form.js
```

Docker k6 fallback:
```powershell
docker run --rm --network whitefly-python-task_default -e BASE_URL=http://nginx/flask -v ${PWD}/load_tests:/scripts grafana/k6 run /scripts/sync_form.js
docker run --rm --network whitefly-python-task_default -e BASE_URL=http://nginx/fastapi -v ${PWD}/load_tests:/scripts grafana/k6 run /scripts/async_form.js
```

Sample result summary (local run):
- sync: 0% failures, p95 ~24ms
- async: 0% failures, p95 ~9ms

## Anti-bot protections (M5)
Implemented protections on both Flask and FastAPI form POST endpoints:
- Honeypot field validation (`honeypot` must be empty)
- Hardened server-side validation:
  - name length and character restrictions
  - email format and length checks
  - disposable email domain blocking
- Rate limiting:
  - applied to `POST /sync-form` and `POST /async-form`
  - shared limiter for both frameworks
  - Redis-backed in containerized mode
  - in-memory fallback if Redis is unavailable
  - returns HTTP `429` with `Retry-After`

Manual rate-limit test:
```powershell
for ($i = 0; $i -lt 25; $i++) {
  curl.exe -i -X POST "http://localhost:8080/flask/sync-form" `
    -H "Content-Type: application/x-www-form-urlencoded" `
    -d "first_name=Ada&last_name=Lovelace&email=ada$i@example.com&honeypot="
}
```

## FingerprintJS integration point
Not implemented by design (to keep M5 minimal), but can be plugged in as follows:
- add hidden field `fingerprint_id` in both form templates
- populate it client-side with FingerprintJS visitor ID
- validate/store it server-side
- include it in rate-limit key (`ip + fingerprint_id`) for better bot differentiation
