from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from flask_app.app.config import Config
from shared.telemetry import configure_tracing, instrument_celery

celery_app = Celery(
    "whitefly_worker",
    broker=Config.CELERY_BROKER_URL,
    backend=Config.CELERY_RESULT_BACKEND,
    include=["worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 3,
        "interval_start": 0,
        "interval_step": 0.5,
        "interval_max": 2,
    },
    # Transient DB retry, late ack, and reject-on-worker-lost are task-level
    # settings. Duplicate-safe redelivery depends on task_id-based idempotency;
    # a real dead-letter queue remains deferred.
    task_ignore_result=True,
)


@worker_process_init.connect(weak=False)
def _init_worker_tracing(*_args, **_kwargs) -> None:
    # Prefork children must build their own exporter thread after the fork.
    configure_tracing("whitefly-worker")
    instrument_celery()
