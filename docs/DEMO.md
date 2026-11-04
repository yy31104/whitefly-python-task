# Demo guide

These examples use a POSIX shell, Docker Compose, and generated form data. Run them from the repository root after installing the local dependencies in the [README](../README.md).

## Start a fresh stack

```bash
cp .env.example .env
docker compose build
docker compose run --rm flask_app alembic upgrade head
docker compose up -d
docker compose ps
```

The one-off migration starts PostgreSQL and Redis dependencies first. The app containers start afterward, so their schema initialization sees the migrated tables.

The image definitions are under `docker/`; each build keeps the repository root as its context. To inspect app health:

```bash
docker inspect --format '{{json .State.Health.Status}}' whitefly-flask
docker inspect --format '{{json .State.Health.Status}}' whitefly-fastapi
```

Open [Flask](http://localhost:8080/flask/) or [FastAPI](http://localhost:8080/fastapi/).

For a local SQLite database, migrate before importing the default app or running tests:

```bash
DATABASE_URL=sqlite:///app.db .venv/bin/alembic upgrade head
```

If an existing database was already created from the current SQLAlchemy metadata, confirm schema parity before marking it current:

```bash
docker compose exec flask_app alembic stamp head
```

## Synchronous submissions

Flask:

```bash
curl -i -L \
  -d 'first_name=Demo&last_name=Viewer&email=demo@example.com&honeypot=' \
  http://localhost:8080/flask/sync-form
```

FastAPI:

```bash
curl -i -L \
  -d 'first_name=Demo&last_name=Viewer&email=demo@example.com&honeypot=' \
  http://localhost:8080/fastapi/sync-form
```

The successful POST redirects after the row is saved. `-L` follows that redirect to the result page.

## Asynchronous submissions and status

Open the [Flask form](http://localhost:8080/flask/async-form) or [FastAPI form](http://localhost:8080/fastapi/async-form). Submit the form and watch its status region poll until a terminal state.

To enqueue from a terminal and inspect the redirect:

```bash
curl -i \
  -d 'first_name=Demo&last_name=Viewer&email=demo@example.com&honeypot=' \
  http://localhost:8080/flask/async-form
```

Copy `task_id` from the `Location` response header into the following variable:

```bash
task_id='replace-with-the-returned-task-id'
curl -s "http://localhost:8080/flask/async-status/$task_id"
curl -s "http://localhost:8080/fastapi/async-status/$task_id"
```

The status response contains lifecycle metadata. Names, emails, and form payloads stay out of that response.

## Follow one request through the logs

```bash
request_id='demo-trace-001'
curl -i \
  -H "X-Request-ID: $request_id" \
  -d 'first_name=Demo&last_name=Viewer&email=demo@example.com&honeypot=' \
  http://localhost:8080/flask/async-form

docker compose logs nginx flask_app worker | grep -F "$request_id"
```

Nginx, the app, and the worker use the same `request_id` for this submission.

## Replay an idempotency key

Run the request twice with the same key and payload:

```bash
idempotency_key='demo-key-001'
for attempt in 1 2; do
  curl -i \
    -H "Idempotency-Key: $idempotency_key" \
    -d 'first_name=Demo&last_name=Viewer&email=demo@example.com&honeypot=' \
    http://localhost:8080/flask/sync-form
done
```

Both requests refer to one stored submission. A changed payload with that same key returns `409`:

```bash
curl -i \
  -H "Idempotency-Key: $idempotency_key" \
  -d 'first_name=Another&last_name=Viewer&email=demo@example.com&honeypot=' \
  http://localhost:8080/flask/sync-form
```

## Worker loss and redelivery

1. Enqueue an asynchronous submission.
2. Stop the worker while processing is in flight:

```bash
docker compose kill worker
```

3. Restart it and inspect the task in the logs:

```bash
docker compose up -d worker
docker compose logs -f worker
```

Late acknowledgement, rejection on worker loss, and task-ID idempotency allow a redelivered task to reuse its earlier submission. Redis visibility timeout can delay the redelivery; this is a manual timing exercise.

## Queue unavailable

```bash
docker compose stop redis
curl -i \
  -d 'first_name=Demo&last_name=Viewer&email=demo@example.com&honeypot=' \
  http://localhost:8080/flask/async-form
docker compose start redis
docker compose up -d worker
```

A failed publish returns `503`. Restart Redis before continuing with other examples.

## Load testing conditions

The scripts use ten virtual users for twenty seconds. The example configuration limits each client and endpoint to twenty POSTs per minute, so a default run exercises rate-limit rejection.

For a throughput experiment, choose an explicit local rate budget that matches the workload, recreate the app services, and record the rate setting alongside the result. Keep the application defaults and the k6 thresholds unchanged in source.

With k6 installed locally:

```bash
k6 run load_tests/sync_form.js
k6 run load_tests/async_form.js
```

Docker can run the same scripts on the stack's network. The following network name assumes the default clone directory `whitefly-python-task`; substitute your Compose project's network if you used another project name:

```bash
docker run --rm --network whitefly-python-task_default \
  -e BASE_URL=http://nginx/flask \
  -v "$PWD/load_tests:/scripts:ro" \
  grafana/k6 run /scripts/sync_form.js

docker run --rm --network whitefly-python-task_default \
  -e BASE_URL=http://nginx/fastapi \
  -v "$PWD/load_tests:/scripts:ro" \
  grafana/k6 run /scripts/async_form.js
```

The default limit is 20 form submissions per minute per IP. Use the configured limits when interpreting load-test results; the summary above prints errors as well as latency.

## Shut down

```bash
docker compose down
```

This retains the local PostgreSQL volume for a later run.
