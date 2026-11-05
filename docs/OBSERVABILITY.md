# Observability

One async form submission produces one trace. It starts at the HTTP request in Flask or FastAPI, continues through the Celery publish, crosses Redis in the message headers, and ends in the worker's database writes.

```text
POST /async-form                         SERVER    Flask or FastAPI
├── SELECT / INSERT idempotency_claims   CLIENT    SQLAlchemy
├── SELECT / INSERT task_statuses        CLIENT    SQLAlchemy ("queued")
└── apply_async/worker.save_async_submission   PRODUCER  Celery publish
    └── run/worker.save_async_submission       CONSUMER  Celery worker
        ├── UPDATE task_statuses               CLIENT    "processing"
        ├── SELECT / INSERT submissions        CLIENT
        └── UPDATE task_statuses               CLIENT    "processed"
```

## How it works

| Piece | Where | What it does |
| --- | --- | --- |
| `configure_tracing(service)` | [`shared/telemetry.py`](../shared/telemetry.py) | Installs an SDK tracer provider with a batching OTLP/HTTP exporter, once per process, only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Otherwise the global provider stays a no-op. |
| Flask | `create_app` | `FlaskInstrumentor.instrument_app(app)` makes a SERVER span per request and reads an incoming `traceparent`. `/healthz` is excluded. |
| FastAPI | `create_app` | `FastAPIInstrumentor.instrument_app(app)`, same exclusions. |
| Celery | apps and `worker_process_init` | `CeleryInstrumentor` hooks `before_task_publish` (PRODUCER span, injects `traceparent` into the message headers) and `task_prerun` (extracts it, CONSUMER span). The worker configures tracing after the prefork fork so each child gets its own exporter thread. |
| SQLAlchemy | `init_database` | One `EngineTracer` per engine, so every statement is a CLIENT span under whatever span is current. |

The service-layer code did not change. The trace context follows the request through Python context variables in the web process and through the message headers between processes.

### What is recorded

- HTTP: method, route, status code, user agent, peer IP. Request bodies and headers are not captured.
- SQL: the statement with `?`/`%(name)s` placeholders. Bound values (names, emails) are not recorded.
- Celery: task name, task id, state, routing metadata. Task arguments are not recorded.

`tests/observability/test_tracing.py` asserts that none of the submitted name or email values appear in any span attribute.

## Run it with Jaeger

`docker compose up -d` also starts `otel-collector` (OTLP on 4317/4318 inside the network) and `jaeger` (UI on [localhost:16686](http://localhost:16686)). The apps and the worker export to `http://otel-collector:4318`; the collector batches and forwards to Jaeger, and logs one line per batch:

```bash
docker compose logs -f otel-collector
```

Submit the async form, open Jaeger, pick service `whitefly-flask` or `whitefly-fastapi`, and the trace includes `whitefly-worker` spans. To turn exporting off, set `OTEL_SDK_DISABLED=true` or leave `OTEL_EXPORTER_OTLP_ENDPOINT` empty.

## Evidence

[`docs/evidence/async-submission-trace.otlp.json`](evidence/async-submission-trace.otlp.json) is a real trace in OTLP/JSON, the body the HTTP exporter would send to the collector. [`scripts/export_trace.py`](../scripts/export_trace.py) produced it by running the real Flask app, publishing through Celery's in-memory broker to an in-thread worker, and encoding the finished spans with the OTLP encoder:

```bash
.venv/bin/python scripts/export_trace.py docs/evidence/async-submission-trace.otlp.json
```

```text
HTTP 302; 12 spans -> docs/evidence/async-submission-trace.otlp.json
40228556  POST /async-form [SERVER] 16.5 ms
40228556    SELECT .../evidence.db [CLIENT] 0.1 ms
40228556    INSERT .../evidence.db [CLIENT] 0.1 ms
40228556    SELECT .../evidence.db [CLIENT] 0.1 ms
40228556    INSERT .../evidence.db [CLIENT] 0.1 ms
40228556    apply_async/worker.save_async_submission [PRODUCER] 0.3 ms
40228556      run/worker.save_async_submission [CONSUMER] 16.4 ms
40228556        UPDATE .../evidence.db [CLIENT] 0.4 ms
40228556        SELECT .../evidence.db [CLIENT] 0.1 ms
40228556        INSERT .../evidence.db [CLIENT] 0.1 ms
40228556        SELECT .../evidence.db [CLIENT] 0.1 ms
40228556        UPDATE .../evidence.db [CLIENT] 0.1 ms
```

All twelve spans carry trace id `402285568ba678e1580ec0b885cbc83f`. The run span's `parentSpanId` is the publish span's `spanId`.

This evidence was not captured from Jaeger: the environment that produced it had no Docker daemon, so the Compose stack with the collector and Jaeger has not been run here. The same JSON file can be replayed into a running collector with `curl -X POST -H 'Content-Type: application/json' --data @docs/evidence/async-submission-trace.otlp.json http://localhost:4318/v1/traces` if port 4318 is published.

## Tests

`tests/observability/test_tracing.py` installs an in-memory span exporter and checks:

- Flask and FastAPI: one async POST yields exactly one SERVER span and one trace id across all spans. The PRODUCER span descends from the SERVER span; the CONSUMER span's parent is the PRODUCER span; request-side and worker-side SQL spans exist and share the trace id; `messaging.message.id` equals the task id.
- An incoming `traceparent` header is continued, not replaced.
- `/healthz` produces no SERVER span.
- No submitted name or email appears in any attribute.

The message really goes through Celery (in-memory broker, in-thread solo worker), so the trace context crosses the broker in the message headers, as it does with Redis.

## Limits

- Only traces. No metrics or log export; JSON log lines do not carry `trace_id` yet.
- Sampling is the SDK default (parent-based, always on). Every request is exported when an endpoint is set.
- `worker_process_init` is not sent for the `solo` pool. A worker started with `--pool=solo` must call `configure_tracing` itself.
- The Jaeger store is in-memory; traces disappear when the container restarts.
- Image tags (`otel/opentelemetry-collector:0.115.0`, `jaegertracing/jaeger:2.5.0`) were pinned without pulling them here.
