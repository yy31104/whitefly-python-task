"""Export one async submission's trace as OTLP/JSON, without Docker.

Runs the real Flask app, publishes through Celery's in-memory broker to an
in-thread worker, and encodes the finished spans with the same OTLP encoder the
HTTP exporter uses. Writes the OTLP/JSON request body and prints the span tree.

    python scripts/export_trace.py docs/evidence/async-submission-trace.otlp.json
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from celery.contrib.testing.worker import start_worker  # noqa: E402
from google.protobuf.json_format import MessageToJson  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.exporter.otlp.proto.common.trace_encoder import (  # noqa: E402
    encode_spans,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

TASK = "worker.save_async_submission"


def main(output: Path) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "whitefly"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    import worker.tasks as worker_tasks
    from flask_app.app import create_app
    from worker.celery_app import celery_app

    db_dir = Path(tempfile.mkdtemp())
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "evidence",
            "DATABASE_URL": f"sqlite:///{(db_dir / 'evidence.db').as_posix()}",
        }
    )
    celery_app.conf.update(broker_url="memory://", result_backend="cache+memory://")
    worker_tasks._db_ready = True

    with start_worker(celery_app, pool="solo", perform_ping_check=False):
        exporter.clear()
        response = app.test_client().post(
            "/async-form",
            data={
                "first_name": "Example",
                "last_name": "Person",
                "email": "person@example.com",
                "idempotency_key": "evidence-key-1",
            },
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if any(s.name == f"run/{TASK}" for s in exporter.get_finished_spans()):
                break
            time.sleep(0.05)

    spans = exporter.get_finished_spans()
    body = json.loads(MessageToJson(encode_spans(spans)))
    _hex_ids(body)
    output.write_text(json.dumps(body, indent=2) + "\n")
    print(f"HTTP {response.status_code}; {len(spans)} spans -> {output}")
    _print_tree(spans)


def _hex_ids(node) -> None:
    # protobuf's JSON mapping writes bytes as base64; OTLP/JSON wants hex ids.
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"traceId", "spanId", "parentSpanId"} and isinstance(value, str):
                node[key] = base64.b64decode(value).hex()
            else:
                _hex_ids(value)
    elif isinstance(node, list):
        for item in node:
            _hex_ids(item)


def _print_tree(spans) -> None:
    children: dict[int | None, list] = {}
    ids = {s.context.span_id for s in spans}
    for span in sorted(spans, key=lambda s: s.start_time):
        parent = span.parent.span_id if span.parent else None
        children.setdefault(parent if parent in ids else None, []).append(span)

    def walk(parent_id, depth):
        for span in children.get(parent_id, []):
            ms = (span.end_time - span.start_time) / 1e6
            print(
                f"{format(span.context.trace_id, '032x')[:8]}  "
                f"{'  ' * depth}{span.name} [{span.kind.name}] {ms:.1f} ms"
            )
            walk(span.context.span_id, depth + 1)

    walk(None, 0)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "trace.otlp.json"))
