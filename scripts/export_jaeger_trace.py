"""Export the latest trace for one operation from a running Jaeger.

Run after `docker compose up -d` and one form submission:

    python scripts/export_jaeger_trace.py whitefly-flask "POST /flask/async-form" \
        docs/evidence/flask-async

Writes two files from Jaeger's query API, unchanged apart from indentation:

- ``<prefix>.jaeger.json``: ``GET /api/traces/{id}`` (Jaeger's own JSON model)
- ``<prefix>.otlp.json``: ``GET /api/v3/traces/{id}``; its ``result`` is OTLP/JSON

and prints the span tree with the service that emitted each span.
Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

JAEGER = os.getenv("JAEGER_QUERY_URL", "http://localhost:16686")


def _get(path: str, **params) -> dict:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    with urllib.request.urlopen(f"{JAEGER}{path}{query}", timeout=10) as response:
        return json.load(response)


def latest_trace_id(service: str, operation: str) -> str:
    found = _get(
        "/api/traces", service=service, operation=operation, limit=20, lookback="1h"
    )["data"]
    if not found:
        raise SystemExit(f"no trace for {service} {operation!r} in the last hour")
    newest = max(found, key=lambda t: min(s["startTime"] for s in t["spans"]))
    return newest["traceID"]


def print_tree(trace: dict) -> None:
    services = {key: p["serviceName"] for key, p in trace["processes"].items()}
    children: dict[str | None, list[dict]] = {}
    for span in trace["spans"]:
        parents = [r["spanID"] for r in span.get("references", [])]
        children.setdefault(parents[0] if parents else None, []).append(span)

    def walk(parent: str | None, depth: int) -> None:
        for span in sorted(children.get(parent, []), key=lambda s: s["startTime"]):
            tags = {t["key"]: t["value"] for t in span["tags"]}
            print(
                f"{'  ' * depth}{span['operationName']} "
                f"[{tags.get('span.kind', '')}] {services[span['processID']]} "
                f"{span['duration'] / 1000:.1f} ms"
            )
            walk(span["spanID"], depth + 1)

    walk(None, 0)


def main(service: str, operation: str, prefix: str) -> None:
    trace_id = latest_trace_id(service, operation)
    jaeger = _get(f"/api/traces/{trace_id}")
    otlp = _get(f"/api/v3/traces/{trace_id}")

    out = Path(prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{prefix}.jaeger.json").write_text(json.dumps(jaeger, indent=2) + "\n")
    Path(f"{prefix}.otlp.json").write_text(json.dumps(otlp, indent=2) + "\n")

    trace = jaeger["data"][0]
    trace_ids = {span["traceID"] for span in trace["spans"]}
    print(f"trace {trace_id}: {len(trace['spans'])} spans, trace ids {trace_ids}")
    print_tree(trace)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
