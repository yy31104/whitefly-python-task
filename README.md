# whitefly-python-task

Python backend recruitment assignment for Whitefly.

## Current status
- M0 done: project skeleton
- M1 done: Flask sync flow
- M2 done: Flask async flow with Celery + Redis
- M3 done: FastAPI equivalent flow
- M4 done: Docker Compose deployment, Nginx reverse proxy, k6 scripts
- M5 pending: anti-bot hardening expansion

## Architecture (M4)
- Nginx (single entrypoint)
- Flask app (sync + async form routes)
- FastAPI app (sync + async form routes)
- Celery worker (async DB writes)
- Redis (broker/result backend)
- PostgreSQL (shared database)

Nginx routing:
- `http://localhost:8080/flask/...` -> Flask service
- `http://localhost:8080/fastapi/...` -> FastAPI service

## Local development (without Docker)
1. Create and activate virtual env.
2. Install dependencies:
```powershell
.\.venv\Scripts\python -m pip install -r requirements\dev.txt
```
3. Run Flask:
```powershell
.\.venv\Scripts\python -m flask --app flask_app.app run --debug
```
4. Run FastAPI:
```powershell
.\.venv\Scripts\python -m uvicorn fastapi_app.app.main:app --reload --port 8000
```
5. Run tests:
```powershell
.\.venv\Scripts\python -m pytest -q
```

## Docker Compose deployment (M4)
1. Create `.env` from `.env.example`.
2. Build and run:
```powershell
docker compose up --build
```
3. Open:
- Flask via Nginx: `http://localhost:8080/flask/`
- FastAPI via Nginx: `http://localhost:8080/fastapi/`
- Flask submissions: `http://localhost:8080/flask/submissions`
- FastAPI submissions: `http://localhost:8080/fastapi/submissions`

## Logs and status
```powershell
docker compose ps
docker compose logs -f
docker compose logs -f worker
docker compose logs -f nginx
```

## k6 load testing
Run sync test (Flask path):
```powershell
k6 run load_tests/sync_form.js
```

Run async test (FastAPI path):
```powershell
k6 run load_tests/async_form.js
```

Optional via Docker k6:
```powershell
docker run --rm --network whitefly-python-task_default -v ${PWD}/load_tests:/scripts grafana/k6 run /scripts/sync_form.js
docker run --rm --network whitefly-python-task_default -v ${PWD}/load_tests:/scripts grafana/k6 run /scripts/async_form.js
```

## Why PostgreSQL in M4
- Better concurrent write behavior than SQLite in multi-container workloads.
- Closer to production backend architecture.
- Easier to explain for deployment and scaling discussions.

## Anti-bot protections (M5)
Current protections for both Flask and FastAPI form flows:
- Honeypot field check on the server side (`honeypot` must stay empty).
- Hardened server-side validation:
  - first/last name length and character checks
  - email normalization and format checks
  - disposable email domain rejection (`mailinator`, `10minutemail`, etc.)
- Rate limiting on `POST /sync-form` and `POST /async-form`:
  - shared limiter implementation used by both frameworks
  - Redis-backed counters in containerized setup
  - in-memory fallback for local environments without Redis
  - returns HTTP `429` with `Retry-After` header when exceeded

Manual rate-limit test (PowerShell):
```powershell
for ($i = 0; $i -lt 25; $i++) {
  curl.exe -i -X POST "http://localhost:8080/flask/sync-form" `
    -H "Content-Type: application/x-www-form-urlencoded" `
    -d "first_name=Ada&last_name=Lovelace&email=ada$i@example.com&honeypot="
}
```

### FingerprintJS integration point
Minimal integration point (not implemented here):
- Add a hidden `fingerprint_id` field to both sync/async forms.
- Populate it in frontend using FingerprintJS visitor ID.
- Include it in server-side payload validation.
- Use `fingerprint_id` as part of the rate-limit key (e.g., `ip + fingerprint_id`) for better bot differentiation behind shared IPs.
