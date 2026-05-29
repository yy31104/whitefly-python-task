# Interview Notes

Personal/portfolio aid: this file is meant to help explain the project in interviews. It is not operational runbook material.

## 60-Second Project Pitch

Whitefly started as a backend assignment with Flask, FastAPI, Celery, Redis, and PostgreSQL. I turned it into a focused backend reliability lab. The core user workflow is still simple: submit a form synchronously or asynchronously. Around that workflow I added request correlation, structured JSON logs, Alembic migrations, idempotency, bounded retry, late-ack redelivery safety, durable task status, read-only status APIs, and small UI polling.

The interesting part is the sequencing: I did not add retry first. I added idempotency before retry, then used task-id based idempotency before enabling late ack and worker-lost redelivery. That makes the reliability features safer instead of just making failures happen more times.

## Why Idempotency Before Retry Before Acks Late

Retry without idempotency can duplicate writes. Late ack without idempotency can also duplicate writes if the worker dies after commit but before ack.

The safe order was:
1. Add schema support for idempotency keys.
2. Centralize service-layer deduplication.
3. Add HTTP and worker propagation.
4. Enable bounded transient DB retry.
5. Enable late ack and worker-lost redelivery.

That order turns retry and redelivery from duplicate risks into convergence mechanisms.

## Why Task ID Works As Fallback Idempotency Key

Celery redelivers the same message with the same task id. If a client did not provide an idempotency key, the worker can use the Celery task id as the effective idempotency key.

That means:
- first execution writes `idempotency_key = task_id`
- redelivery with the same task id looks up the existing row
- same payload returns the same submission
- no duplicate row is created

## Why Task Status Has Its Own Table

An async `Submission` row does not exist until the worker successfully persists it. A task can fail, conflict, retry, or be queued before any submission row exists.

Putting lifecycle on `Submission.status` would miss those cases. A separate `task_statuses` table keyed by Celery `task_id` can represent queued, processing, retrying, processed, failed, and conflict even when there is no submission row.

## Why `task_ignore_result` Stays True

The app owns user-visible task status in PostgreSQL. Celery's result backend is not used as the product status surface.

Keeping `task_ignore_result=True` avoids changing result-backend semantics and keeps task visibility in the domain database, where it can be queried by the app.

## Why Only OperationalError Retries

`OperationalError` represents transient database connectivity or availability failures that may succeed later.

The worker does not retry:
- validation errors
- idempotency conflicts
- integrity errors
- generic exceptions

Those are more likely deterministic or require human/software correction. Retrying them can create noisy loops.

## Why Status Writes Are Best-Effort

Status recording should improve visibility, not break the main workflow. If a status write fails but enqueue or persistence succeeds, the primary business action should still complete.

The tradeoff is eventual consistency: a fresh status lookup can return 404 or show an older state. The UI handles that by polling.

## How This Demonstrates Backend Reliability

This project shows a reliability mindset in layers:
- observability first, so failures can be traced
- migrations before schema evolution
- idempotency before retry
- bounded retry before late ack
- durable task status before UI polling
- tests around failure modes, not just happy paths

It is small enough to explain in an interview but realistic enough to discuss production tradeoffs.

## What Remains Next

- Add auth for internal status/submissions surfaces.
- Add a real dead-letter queue and failure triage workflow.
- Add metrics and tracing backend integration.
- Add GET polling rate limits.
- Move inline JavaScript into a static asset with CSP.
- Tune Celery worker concurrency and prefetch for production load.
