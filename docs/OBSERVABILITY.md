# Observability

One async form submission produces one trace. It starts at the HTTP request in Flask or FastAPI, continues through the Celery publish, crosses Redis in the message headers, and ends in the worker's database writes. Each span carries the name of the service that emitted it, so Jaeger shows `whitefly-flask` (or `whitefly-fastapi`) and `whitefly-worker` in the same trace.

```text
POST /flask/async-form                       SERVER    whitefly-flask
├── SELECT / INSERT idempotency_claims       CLIENT    SQLAlchemy
├── SELECT / INSERT task_statuses            CLIENT    SQLAlchemy ("queued")
└── apply_async/worker.save_async_submission PRODUCER  Celery publish
    └── run/worker.save_async_submission     CONSUMER  whitefly-worker
        ├── UPDATE task_statuses             CLIENT    "processing"
        ├── SELECT / INSERT submissions      CLIENT
        └── UPDATE task_statuses             CLIENT    "processed"
```

## How it works

| Piece | Where | What it does |
| --- | --- | --- |
| `configure_tracing(service)` | [`shared/telemetry.py`](../shared/telemetry.py) | Installs an SDK tracer provider with a batching OTLP/HTTP exporter, once per process, only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Otherwise the global provider stays a no-op. |
| Flask | `create_app` | `FlaskInstrumentor.instrument_app(app)` makes a SERVER span per request and reads an incoming `traceparent`. `/healthz` is excluded. |
| FastAPI | `create_app` | `FastAPIInstrumentor.instrument_app(app)`, same exclusions. It also emits short INTERNAL `http receive` / `http send` spans for the ASGI messages. |
| Celery | apps and `worker_process_init` | `CeleryInstrumentor` hooks `before_task_publish` (PRODUCER span, injects `traceparent` into the message headers) and `task_prerun` (extracts it, CONSUMER span). The worker configures tracing after the prefork fork so each child gets its own exporter thread. |
| SQLAlchemy | `init_database` | One `EngineTracer` per engine, so every statement is a CLIENT span under whatever span is current. |

The service-layer code did not change. The trace context follows the request through Python context variables in the web process and through the message headers between processes.

### What is recorded

- HTTP: method, route, status code, user agent, peer IP. Request bodies and headers are not captured.
- SQL: the statement with `%(name)s` placeholders. Bound values (names, emails) are not recorded.
- Celery: task name, task id, state, routing metadata. Task arguments are not recorded.

`tests/observability/test_tracing.py` asserts that none of the submitted name or email values appear in any span attribute. The exported traces below contain none of the submitted values either.

## Run it with Jaeger

`docker compose up -d` also starts `otel-collector` (OTLP on 4317/4318 inside the Compose network) and `jaeger` (UI and query API on [127.0.0.1:16686](http://localhost:16686)). The apps and the worker export to `http://otel-collector:4318`; the collector batches and forwards to Jaeger, and logs one line per batch:

```bash
docker compose logs -f otel-collector
```

Submit the async form, open Jaeger, and pick service `whitefly-flask` with operation `POST /flask/async-form`, or service `whitefly-fastapi` with operation `POST /fastapi/async-form`. The trace includes the `whitefly-worker` spans. To turn exporting off, set `OTEL_SDK_DISABLED=true` or leave `OTEL_EXPORTER_OTLP_ENDPOINT` empty.

## Evidence

Both traces below were captured on 2026-11-05 from the Compose stack in this repository (PostgreSQL, Redis, Nginx, gunicorn, uvicorn, a prefork Celery worker, the collector and Jaeger). Each was produced by one form POST through Nginx on port 8080, and was exported from Jaeger's query API with [`scripts/export_jaeger_trace.py`](../scripts/export_jaeger_trace.py):

```bash
python scripts/export_jaeger_trace.py whitefly-flask "POST /flask/async-form" docs/evidence/flask-async
python scripts/export_jaeger_trace.py whitefly-fastapi "POST /fastapi/async-form" docs/evidence/fastapi-async
```

Each run writes two files, unchanged apart from indentation:

- `*.jaeger.json`: `GET /api/traces/{id}`, Jaeger's own JSON model.
- `*.otlp.json`: `GET /api/v3/traces/{id}`. Its `result` is OTLP/JSON `TracesData`.

### Flask: [`flask-async.jaeger.json`](evidence/flask-async.jaeger.json), [`flask-async.otlp.json`](evidence/flask-async.otlp.json)

```text
trace f4dda3f9e64d6038e8036c296bad1a6e: 12 spans, trace ids {'f4dda3f9e64d6038e8036c296bad1a6e'}
POST /flask/async-form [server] whitefly-flask 40.2 ms
  SELECT whitefly [client] whitefly-flask 1.1 ms
  INSERT whitefly [client] whitefly-flask 0.4 ms
  SELECT whitefly [client] whitefly-flask 0.7 ms
  INSERT whitefly [client] whitefly-flask 0.5 ms
  apply_async/worker.save_async_submission [producer] whitefly-flask 5.5 ms
    run/worker.save_async_submission [consumer] whitefly-worker 71.3 ms
      SELECT whitefly [client] whitefly-worker 3.4 ms
      SELECT whitefly [client] whitefly-worker 1.8 ms
      INSERT whitefly [client] whitefly-worker 0.6 ms
      SELECT whitefly [client] whitefly-worker 0.4 ms
      UPDATE whitefly [client] whitefly-worker 1.0 ms
```

### FastAPI: [`fastapi-async.jaeger.json`](evidence/fastapi-async.jaeger.json), [`fastapi-async.otlp.json`](evidence/fastapi-async.otlp.json)

```text
trace ba40e16c062b90fbf230e5cd9b2e2b1c: 15 spans, trace ids {'ba40e16c062b90fbf230e5cd9b2e2b1c'}
POST /fastapi/async-form [server] whitefly-fastapi 82.1 ms
  POST /fastapi/async-form http receive [internal] whitefly-fastapi 0.0 ms
  SELECT whitefly [client] whitefly-fastapi 1.1 ms
  INSERT whitefly [client] whitefly-fastapi 0.4 ms
  SELECT whitefly [client] whitefly-fastapi 0.7 ms
  INSERT whitefly [client] whitefly-fastapi 0.5 ms
  apply_async/worker.save_async_submission [producer] whitefly-fastapi 5.1 ms
    run/worker.save_async_submission [consumer] whitefly-worker 11.7 ms
      UPDATE whitefly [client] whitefly-worker 0.7 ms
      SELECT whitefly [client] whitefly-worker 0.4 ms
      INSERT whitefly [client] whitefly-worker 0.4 ms
      SELECT whitefly [client] whitefly-worker 0.3 ms
      UPDATE whitefly [client] whitefly-worker 0.4 ms
  POST /fastapi/async-form http send [internal] whitefly-fastapi 0.0 ms
  POST /fastapi/async-form http send [internal] whitefly-fastapi 0.0 ms
```

### What the Flask trace showed

The two worker subtrees differ, and the difference was a worker bug, not a tracing artefact.

The Flask submission was the first task handled by worker child `ForkPoolWorker-4`. Its first worker span is a `pg_catalog` table check from `init_database()`, and it has no `UPDATE task_statuses` for `processing`. The worker log for that task shows why:

```text
"message":"task status write failed","event":"task_status_write_failed","status":"processing","error_type":"RuntimeError"
```

When these traces were exported, [`worker/tasks.py`](../worker/tasks.py) wrote the `processing` status before it called `_ensure_database_ready()`. In a freshly forked child the engine did not exist yet, so the best-effort write raised `RuntimeError("Database not initialized")`, which was logged and swallowed. The task's status then went straight from `queued` to `processed`.

The FastAPI submission was the second task on the same child, so it shows the `processing` UPDATE. The in-process tests never hit this, because their engine is already initialized. The worker now initializes the database before writing `processing`, and [`tests/worker/test_first_task.py`](../tests/worker/test_first_task.py) checks it in a real forked child.

### Break it, on the running stack

1. Stop the worker and start an untraced one:

   ```bash
   docker compose stop worker
   docker compose run -d --no-deps -e OTEL_EXPORTER_OTLP_ENDPOINT= worker
   ```

2. Submit the Flask async form again.

The task still reached `processed`, but Jaeger's trace for the request stopped at the PRODUCER span:

```text
trace 0175914108caf61ead4d421ba6d9ebb7: 6 spans, trace ids {'0175914108caf61ead4d421ba6d9ebb7'}
POST /flask/async-form [server] whitefly-flask 9.7 ms
  SELECT whitefly [client] whitefly-flask 0.4 ms
  INSERT whitefly [client] whitefly-flask 0.3 ms
  SELECT whitefly [client] whitefly-flask 0.5 ms
  INSERT whitefly [client] whitefly-flask 0.3 ms
  apply_async/worker.save_async_submission [producer] whitefly-flask 0.9 ms
```

## Tests

`tests/observability/test_tracing.py` installs an in-memory span exporter and checks:

- **Flask and FastAPI async POSTs:**
  - exactly one SERVER span, and one trace id across all spans;
  - the PRODUCER span descends from the SERVER span, and the CONSUMER span's parent is the PRODUCER span;
  - request-side and worker-side SQL spans exist and share the trace id;
  - `messaging.message.id` equals the task id.
- An incoming `traceparent` header is continued, not replaced.
- `/healthz` produces no SERVER span.
- No submitted name or email appears in any attribute.

The message really goes through Celery (in-memory broker, in-thread solo worker), so the trace context crosses the broker in the message headers, as it does with Redis.

## Limits

- Only traces. No metrics or log export; JSON log lines do not carry `trace_id` yet.
- Sampling is the SDK default (parent-based, always on). Every request is exported when an endpoint is set.
- `worker_process_init` is not sent for the `solo` pool. A worker started with `--pool=solo` must call `configure_tracing` itself.
- Jaeger uses in-memory storage; traces disappear when the container restarts.
- Span names for SQL are `<operation> <database>` (for example `SELECT whitefly`), so telling a claim insert from a submission insert needs the `db.statement` attribute.
