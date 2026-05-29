from __future__ import annotations

import json
import logging
import uuid

from shared.logging import (
    JsonLineFormatter,
    clear_request_id,
    configure_logging,
    resolve_request_id,
    sanitize_request_id,
    set_request_id,
)


def test_json_formatter_includes_request_id_and_extra_fields():
    configure_logging()
    set_request_id("json-request-id")
    try:
        logger = logging.getLogger("tests.logging")
        record = logger.makeRecord(
            name=logger.name,
            level=logging.INFO,
            fn=__file__,
            lno=10,
            msg="structured event",
            args=(),
            exc_info=None,
            func="test_json_formatter_includes_request_id_and_extra_fields",
            extra={"event": "test_event", "status_code": 200},
        )
        payload = json.loads(JsonLineFormatter().format(record))
    finally:
        clear_request_id()

    assert payload["level"] == "INFO"
    assert payload["logger"] == "tests.logging"
    assert payload["message"] == "structured event"
    assert payload["request_id"] == "json-request-id"
    assert payload["event"] == "test_event"
    assert payload["status_code"] == 200


def test_request_id_validation_rejects_control_chars_and_long_values():
    malicious_values = [
        "bad\nrequest",
        "bad\rrequest",
        "bad\x1frequest",
        "x" * 129,
        "not allowed",
    ]

    for value in malicious_values:
        assert sanitize_request_id(value) is None
        resolved = resolve_request_id(value)
        assert resolved != value
        assert uuid.UUID(resolved)


def test_set_request_id_does_not_log_unsafe_value():
    configure_logging()
    malicious = "evil\nrequest"
    set_request_id(malicious)
    try:
        logger = logging.getLogger("tests.logging")
        record = logger.makeRecord(
            name=logger.name,
            level=logging.INFO,
            fn=__file__,
            lno=10,
            msg="safe event",
            args=(),
            exc_info=None,
            func="test_set_request_id_does_not_log_unsafe_value",
            extra={},
        )
        payload = json.loads(JsonLineFormatter().format(record))
    finally:
        clear_request_id()

    assert "request_id" not in payload
    assert malicious not in json.dumps(payload)


def test_configure_logging_is_idempotent():
    root_logger = logging.getLogger()

    configure_logging()
    first_handlers = [
        handler
        for handler in root_logger.handlers
        if getattr(handler, "_whitefly_json_handler", False)
    ]

    configure_logging()
    second_handlers = [
        handler
        for handler in root_logger.handlers
        if getattr(handler, "_whitefly_json_handler", False)
    ]

    assert len(first_handlers) == 1
    assert second_handlers == first_handlers
