# Architecture

The same form-submission workflow is implemented in Flask and FastAPI. Shared modules handle validation, persistence, idempotency, request correlation, and task status; Celery processes queued submissions.

## System Topology

```mermaid
flowchart LR
    Client[Client or browser]
    Nginx[Nginx reverse proxy]
    Flask[Flask app]
    FastAPI[FastAPI app]
    Redis[Redis broker and rate-limit store]
    Postgres[PostgreSQL database]
    Worker[Celery worker]

    Client --> Nginx
    Nginx --> Flask
    Nginx --> FastAPI
    Flask --> Postgres
    FastAPI --> Postgres
    Flask --> Redis
    FastAPI --> Redis
    Redis --> Worker
    Worker --> Postgres
```

Runtime routing:
- `/flask/...` goes to the Flask service.
- `/fastapi/...` goes to the FastAPI service.
- The worker consumes async messages from Redis and writes to PostgreSQL.

## Dual-Framework Parity

The project intentionally keeps Flask and FastAPI side by side. That makes the shared reliability work easier to evaluate because both frameworks use the same validation, persistence, idempotency, task-status, and database layers while still exercising different HTTP stacks.

The parity goal is not to duplicate business logic. The framework routes stay thin; the shared modules carry the behavior that must remain consistent.

## Sync Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant N as Nginx
    participant A as Flask or FastAPI
    participant S as shared/services.py
    participant DB as PostgreSQL

    C->>N: POST /flask/sync-form or /fastapi/sync-form
    N->>A: Forward request with X-Request-ID
    A->>S: Validate form and resolve Idempotency-Key
    S->>DB: Insert or reuse Submission
    DB-->>S: Submission row
    S-->>A: Submission
    A-->>N: Redirect or 409 conflict
    N-->>C: HTTP response with X-Request-ID
```

Sync idempotency is handled in the service layer. A safe key with the same payload reuses the existing row; the same key with a different payload becomes a conflict.

## Async Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant N as Nginx
    participant A as Flask or FastAPI
    participant S as shared/services.py
    participant R as Redis
    participant W as Celery worker
    participant DB as PostgreSQL
    participant TS as task_statuses

    C->>N: POST /async-form
    N->>A: Forward request with X-Request-ID
    A->>S: Validate form and resolve idempotency key
    S->>R: Enqueue Celery task
    S->>TS: Record queued
    A-->>C: Redirect to async form with task_id
    C->>A: GET async form with task_id
    A-->>C: Render polling region and status URL
    C->>A: Poll /async-status/{task_id}
    A->>TS: Read current status
    TS-->>A: queued or processing or retrying or processed or failed or conflict
    A-->>C: JSON status
    R->>W: Deliver task
    W->>TS: Record processing
    W->>DB: Persist Submission with effective idempotency key
    alt transient OperationalError
        W->>TS: Record retrying
        W->>R: Schedule bounded retry
    else success
        DB-->>W: Submission row
        W->>TS: Record processed with submission_id
    else terminal failure
        W->>TS: Record failed or conflict
    end
```

The browser polling is deliberately simple: after enqueue, the async form polls every two seconds and stops on terminal states.

## Async Task State Machine

```mermaid
stateDiagram-v2
    [*] --> queued: enqueue succeeds
    queued --> processing: worker starts
    processing --> retrying: transient OperationalError
    retrying --> processing: Celery retry
    processing --> processed: submission saved
    processing --> failed: terminal failure
    processing --> conflict: async IdempotencyConflict
    retrying --> failed: retries exhausted
    processed --> [*]
    failed --> [*]
    conflict --> [*]
```

## Data Model Overview

### `submissions`

`submissions` stores successful sync and async form submissions. It includes:
- name and email fields from the form
- `form_type`
- `status`
- nullable unique `idempotency_key`
- `created_at`

The `idempotency_key` is nullable so old/keyless sync behavior can still insert multiple rows. Non-null keys are unique so replayed requests converge on one stored submission.

### `task_statuses`

`task_statuses` stores async task lifecycle by Celery `task_id`. It includes:
- `task_id`
- `status`
- optional `submission_id`
- optional `error_type`
- `retries`
- `created_at`
- `updated_at`

There is intentionally no foreign key. Failed or conflicted async tasks may never create a submission, and the status row still needs to exist.

## Why A Separate Task Status Table

Task status does not live on `Submission.status` because an async `Submission` row only exists after the worker succeeds. A failure, conflict, or worker-loss scenario can happen before there is any submission row to update.

Task status also does not use the Celery result backend. The worker keeps `task_ignore_result=True` so application visibility is owned by the domain database, not by broker/result-backend retention behavior. The `task_statuses` table gives the app a durable, queryable status surface while keeping Celery result semantics unchanged.
