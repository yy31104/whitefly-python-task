# Demo Script

This demo is PowerShell-first and assumes Docker Desktop is running.

## Start The Stack

```powershell
Copy-Item .env.example .env -Force
docker compose up -d --build
docker compose ps
```

Expected containers:
- `whitefly-nginx`
- `whitefly-flask`
- `whitefly-fastapi`
- `whitefly-worker`
- `whitefly-redis`
- `whitefly-postgres`

Open:
- Flask: `http://localhost:8080/flask/`
- FastAPI: `http://localhost:8080/fastapi/`

## Sync Form Happy Path

```powershell
curl.exe -i -L `
  -d "first_name=Grace&last_name=Hopper&email=grace@example.com&honeypot=" `
  http://localhost:8080/flask/sync-form
```

FastAPI parity:

```powershell
curl.exe -i -L `
  -d "first_name=Grace&last_name=Hopper&email=grace@example.com&honeypot=" `
  http://localhost:8080/fastapi/sync-form
```

## Async Form Happy Path With UI Polling

Open the async form in a browser:

```powershell
Start-Process http://localhost:8080/flask/async-form
Start-Process http://localhost:8080/fastapi/async-form
```

Submit the form. The page redirects back with a `task_id` query parameter and shows a small status region. The browser polls every two seconds until it reaches a terminal state.

CLI enqueue example:

```powershell
curl.exe -i -L `
  -d "first_name=Grace&last_name=Hopper&email=grace@example.com&honeypot=" `
  http://localhost:8080/flask/async-form
```

## Request Correlation Demo

```powershell
$requestId = "demo-trace-001"

curl.exe -i `
  -H "X-Request-ID: $requestId" `
  -d "first_name=Grace&last_name=Hopper&email=grace@example.com&honeypot=" `
  http://localhost:8080/flask/async-form

docker logs whitefly-nginx | Select-String $requestId
docker logs whitefly-flask | Select-String $requestId
docker logs whitefly-worker | Select-String $requestId
```

The same `request_id` should appear in nginx, app, and worker logs.

## Idempotency-Key Replay Demo

Same key and same payload should converge on one logical submission.

```powershell
$key = "demo-key-001"

curl.exe -i -L `
  -H "Idempotency-Key: $key" `
  -d "first_name=Grace&last_name=Hopper&email=grace@example.com&honeypot=" `
  http://localhost:8080/flask/sync-form

curl.exe -i -L `
  -H "Idempotency-Key: $key" `
  -d "first_name=Grace&last_name=Hopper&email=grace@example.com&honeypot=" `
  http://localhost:8080/flask/sync-form
```

## Same-Key Different-Payload Conflict Demo

```powershell
$conflictKey = "demo-conflict-001"

curl.exe -i `
  -H "Idempotency-Key: $conflictKey" `
  -d "first_name=Grace&last_name=Hopper&email=demo@example.com&honeypot=" `
  http://localhost:8080/flask/sync-form

curl.exe -i `
  -H "Idempotency-Key: $conflictKey" `
  -d "first_name=Ada&last_name=Lovelace&email=demo@example.com&honeypot=" `
  http://localhost:8080/flask/sync-form
```

The second request should return `409`.

## Status Endpoint Example

If the async redirect shows `task_id=abc123`, query status directly:

```powershell
$taskId = "abc123"
Invoke-WebRequest `
  -Headers @{ Accept = "application/json" } `
  -Uri "http://localhost:8080/flask/async-status/$taskId" |
  Select-Object -ExpandProperty Content
```

FastAPI equivalent:

```powershell
Invoke-WebRequest `
  -Headers @{ Accept = "application/json" } `
  -Uri "http://localhost:8080/fastapi/async-status/$taskId" |
  Select-Object -ExpandProperty Content
```

## Worker Lost / Redelivery Demo

This demonstrates the operational shape. The exact timing is intentionally manual.

1. Start an async submission from the browser or CLI.
2. Kill the worker container:

```powershell
docker compose kill worker
docker compose ps
```

3. Restart the worker:

```powershell
docker compose up -d worker
docker compose logs -f worker
```

Because `acks_late=True`, `reject_on_worker_lost=True`, and task-id based idempotency are enabled, a redelivered task can run again without duplicating the submission.

## Redis Down / Queue Unavailable Demo

```powershell
docker compose stop redis

curl.exe -i `
  -d "first_name=Grace&last_name=Hopper&email=grace@example.com&honeypot=" `
  http://localhost:8080/flask/async-form

docker compose start redis
docker compose up -d worker
```

When Redis is down, async enqueue should fail in a controlled way instead of silently accepting work that was not queued.

## Shut Down

```powershell
docker compose down
```
