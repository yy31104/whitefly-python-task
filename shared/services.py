from __future__ import annotations

import logging
from collections.abc import Sequence

from kombu.exceptions import OperationalError
from sqlalchemy.exc import IntegrityError

from shared.db import session_scope
from shared.logging import get_request_id
from shared.models import Submission
from shared.validation import validate_submission_payload

logger = logging.getLogger(__name__)


class QueueUnavailable(RuntimeError):
    """Raised when the async queue is temporarily unavailable."""


class IdempotencyConflict(Exception):
    """Raised when an idempotency key is reused with a different payload."""


DEFAULT_SUBMISSIONS_LIMIT = 200


def validate_submission_data(
    first_name: str,
    last_name: str,
    email: str,
    honeypot: str = "",
) -> dict[str, str]:
    return validate_submission_payload(
        {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "honeypot": honeypot,
        }
    )


def _persist_submission(
    validated_payload: dict[str, str],
    *,
    form_type: str,
    status: str,
    idempotency_key: str | None = None,
) -> Submission:
    resolved_idempotency_key = _normalize_idempotency_key(idempotency_key)

    with session_scope() as session:
        if resolved_idempotency_key:
            existing = _find_submission_by_idempotency_key(
                session,
                resolved_idempotency_key,
            )
            if existing is not None:
                if _payload_matches(existing, validated_payload):
                    _log_idempotent_event(
                        "submission_idempotent_reuse",
                        existing,
                        resolved_idempotency_key,
                        form_type,
                    )
                    return existing

                _log_idempotent_event(
                    "submission_idempotency_conflict",
                    existing,
                    resolved_idempotency_key,
                    form_type,
                )
                raise IdempotencyConflict(
                    "Idempotency key already exists for a different submission."
                )

        submission = Submission(
            first_name=validated_payload["first_name"],
            last_name=validated_payload["last_name"],
            email=validated_payload["email"],
            form_type=form_type,
            status=status,
            idempotency_key=resolved_idempotency_key,
        )
        session.add(submission)

        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if not resolved_idempotency_key:
                raise

            existing = _find_submission_by_idempotency_key(
                session,
                resolved_idempotency_key,
            )
            if existing is not None and _payload_matches(existing, validated_payload):
                _log_idempotent_event(
                    "submission_idempotent_race_resolved",
                    existing,
                    resolved_idempotency_key,
                    form_type,
                )
                return existing

            if existing is not None:
                _log_idempotent_event(
                    "submission_idempotency_conflict",
                    existing,
                    resolved_idempotency_key,
                    form_type,
                )
                raise IdempotencyConflict(
                    "Idempotency key already exists for a different submission."
                ) from exc

            raise

        session.refresh(submission)
        return submission


def _normalize_idempotency_key(idempotency_key: str | None) -> str | None:
    value = (idempotency_key or "").strip()
    return value or None


def _find_submission_by_idempotency_key(
    session,
    idempotency_key: str,
) -> Submission | None:
    return (
        session.query(Submission)
        .filter(Submission.idempotency_key == idempotency_key)
        .one_or_none()
    )


def _payload_matches(
    submission: Submission,
    validated_payload: dict[str, str],
) -> bool:
    return (
        submission.first_name == validated_payload["first_name"]
        and submission.last_name == validated_payload["last_name"]
        and submission.email == validated_payload["email"]
    )


def _log_idempotent_event(
    event: str,
    submission: Submission,
    idempotency_key: str,
    form_type: str,
) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "idempotency_key": idempotency_key,
            "submission_id": submission.id,
            "form_type": form_type,
        },
    )


def create_submission_sync(
    first_name: str,
    last_name: str,
    email: str,
    honeypot: str = "",
    *,
    idempotency_key: str | None = None,
) -> Submission:
    validated = validate_submission_data(first_name, last_name, email, honeypot)
    return _persist_submission(
        validated,
        form_type="sync",
        status="processed",
        idempotency_key=idempotency_key,
    )


def enqueue_submission_async(
    first_name: str,
    last_name: str,
    email: str,
    honeypot: str = "",
) -> str:
    validated = validate_submission_data(first_name, last_name, email, honeypot)

    # Local import avoids circular imports between task and service modules.
    from worker.tasks import save_async_submission_task

    task_kwargs = {
        "first_name": validated["first_name"],
        "last_name": validated["last_name"],
        "email": validated["email"],
    }
    request_id = get_request_id()
    if request_id:
        task_kwargs["request_id"] = request_id

    try:
        task = save_async_submission_task.delay(**task_kwargs)
    except OperationalError as exc:
        raise QueueUnavailable(
            "Queue is temporarily unavailable. Please try again shortly."
        ) from exc
    return task.id


def save_submission_from_worker(
    first_name: str,
    last_name: str,
    email: str,
    *,
    form_type: str = "async",
    status: str = "processed",
    idempotency_key: str | None = None,
) -> Submission:
    validated = validate_submission_data(first_name, last_name, email, honeypot="")
    return _persist_submission(
        validated,
        form_type=form_type,
        status=status,
        idempotency_key=idempotency_key,
    )


def list_submissions(
    limit: int | None = DEFAULT_SUBMISSIONS_LIMIT,
) -> Sequence[Submission]:
    resolved_limit = DEFAULT_SUBMISSIONS_LIMIT if limit is None else max(1, int(limit))
    with session_scope() as session:
        return (
            session.query(Submission)
            .order_by(Submission.created_at.desc())
            .limit(resolved_limit)
            .all()
        )
