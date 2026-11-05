"""OpenTelemetry tracing for the Flask app, the FastAPI app and the Celery worker.

Instrumentation is always installed, because it costs almost nothing while the
global tracer provider is the default no-op one. An SDK tracer provider with an
OTLP exporter is configured only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` (or the
traces-specific variant) is set, so local runs and tests export nothing unless
they ask to.

Span attributes never carry form fields. SQLAlchemy spans record the statement
with placeholders, not the bound values, and Celery spans record task metadata,
not task arguments.
"""

from __future__ import annotations

import logging
import os
import threading

from opentelemetry import metrics, trace

logger = logging.getLogger(__name__)

_EXCLUDED_URLS = "healthz"
_lock = threading.Lock()
_provider_configured = False
_celery_instrumented = False


def _otlp_endpoint_configured() -> bool:
    return bool(
        os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    )


def _sdk_disabled() -> bool:
    return os.getenv("OTEL_SDK_DISABLED", "").strip().lower() == "true"


def configure_tracing(service_name: str) -> bool:
    """Install an OTLP-exporting tracer provider once per process.

    Returns True when this call installed the provider. The service name comes
    from ``OTEL_SERVICE_NAME`` when set, otherwise from ``service_name``.
    """
    global _provider_configured
    if _sdk_disabled() or not _otlp_endpoint_configured():
        return False
    with _lock:
        if _provider_configured:
            return False
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME") or service_name}
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        _provider_configured = True
    logger.info(
        "tracing configured",
        extra={"event": "tracing_configured", "service_name": service_name},
    )
    return True


def instrument_flask_app(app) -> None:
    from opentelemetry.instrumentation.flask import FlaskInstrumentor

    FlaskInstrumentor.instrument_app(app, excluded_urls=_EXCLUDED_URLS)


def instrument_fastapi_app(app) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls=_EXCLUDED_URLS)


def instrument_celery() -> None:
    """Hook Celery's publish and task signals (both producer and worker side).

    The producer side injects the W3C ``traceparent`` into the message headers;
    the worker side extracts it, so the task span joins the HTTP request's trace.
    """
    global _celery_instrumented
    with _lock:
        if _celery_instrumented:
            return
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()
        _celery_instrumented = True


def instrument_engine(engine) -> None:
    """Trace every statement on this engine.

    ``SQLAlchemyInstrumentor().instrument(engine=...)`` only works once per
    process, but ``init_database`` builds a new engine per call (the tests do
    this per test), so each engine gets its own ``EngineTracer``.
    """
    from opentelemetry.instrumentation.sqlalchemy import __version__ as version
    from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer

    tracer = trace.get_tracer("opentelemetry.instrumentation.sqlalchemy", version)
    meter = metrics.get_meter("opentelemetry.instrumentation.sqlalchemy", version)
    connections_usage = meter.create_up_down_counter(
        name="db.client.connections.usage",
        unit="connections",
        description="Number of connections that are currently in the given state",
    )
    EngineTracer(tracer, engine, connections_usage)


def tracer() -> trace.Tracer:
    return trace.get_tracer("whitefly")
