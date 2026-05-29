# Limitations And Operational Notes

This project is intentionally a reliability lab, not a finished production platform.

## Current Limitations

- There is no real dead-letter queue yet.
- There is no auth on dev/internal status or submissions surfaces.
- Redis broker hard-crash redelivery can be delayed by the broker visibility timeout rather than immediate.
- Task status writes are best-effort and eventually consistent.
- Alembic is not auto-run during app startup or container startup.
- The async polling UI uses inline JavaScript because there is no CSP/static-JS setup yet.
- There is no JavaScript test harness.
- Async keyless rows use the Celery `task_id` as the effective idempotency key; sync keyless rows may have `NULL` `idempotency_key`.
- `error_type` exposes exception class names.
- There is no metrics or tracing backend.
- There is no GET polling rate limit for the status endpoint.
- The worker setup is single-worker oriented and does not tune prefetch for production load.

## Rollback And Failure Playbook

### Image rollback

Roll back the deployed image or branch first. Then restart the app and worker containers:

```powershell
docker compose up -d --build flask_app fastapi_app worker
docker compose ps
```

### Alembic downgrade

If the task-status schema must be rolled back:

```powershell
docker compose exec flask_app alembic downgrade 20260529_0002
```

Confirm the expected migration head for the deployed version before downgrading in a shared environment.

### Restart worker

```powershell
docker compose restart worker
docker compose logs -f worker
```

### Triage missing async tasks

Use three sources together:

1. Logs: search by `request_id` and `task_id`.
2. `task_statuses`: check whether the task reached `queued`, `processing`, `retrying`, `processed`, `failed`, or `conflict`.
3. Redis/Postgres: confirm whether the broker was available and whether the submission row exists.

For Docker Compose diagnostics:

```powershell
docker logs whitefly-nginx
docker logs whitefly-flask
docker logs whitefly-fastapi
docker logs whitefly-worker
docker logs whitefly-redis
docker logs whitefly-postgres
docker compose ps
```
