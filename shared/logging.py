from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id",
    default=None,
)


def generate_request_id() -> str:
    return str(uuid.uuid4())


def sanitize_request_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if not _REQUEST_ID_PATTERN.fullmatch(value):
        return None
    return value


def resolve_request_id(value: str | None) -> str:
    return sanitize_request_id(value) or generate_request_id()


def set_request_id(value: str | None) -> contextvars.Token[str | None]:
    return _request_id_var.set(sanitize_request_id(value))


def get_request_id() -> str | None:
    return _request_id_var.get()


def clear_request_id() -> None:
    _request_id_var.set(None)


class JsonLineFormatter(logging.Formatter):
    _reserved_attrs = set(logging.makeLogRecord({}).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = sanitize_request_id(getattr(record, "request_id", None))
        if request_id:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if (
                key in self._reserved_attrs
                or key == "request_id"
                or key.startswith("_")
            ):
                continue
            payload[key] = _json_safe(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: int = logging.INFO) -> None:
    _install_request_id_record_factory()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    whitefly_handlers = [
        handler
        for handler in root_logger.handlers
        if getattr(handler, "_whitefly_json_handler", False)
    ]
    if whitefly_handlers:
        primary_handler = whitefly_handlers[0]
        primary_handler.setLevel(level)
        primary_handler.setFormatter(JsonLineFormatter())
        for duplicate_handler in whitefly_handlers[1:]:
            root_logger.removeHandler(duplicate_handler)
            duplicate_handler.close()
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(JsonLineFormatter())
    handler._whitefly_json_handler = True  # type: ignore[attr-defined]
    root_logger.addHandler(handler)


def _install_request_id_record_factory() -> None:
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_whitefly_request_id_factory", False):
        return

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current_factory(*args, **kwargs)
        record.request_id = get_request_id()
        return record

    record_factory._whitefly_request_id_factory = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(record_factory)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)
