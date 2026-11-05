from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from kombu.exceptions import OperationalError
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from shared.db import session_scope
from shared.idempotency import request_fingerprint
from shared.logging import get_request_id
from shared.models import IdempotencyClaim, Submission, TaskStatus
from shared.task_status import (
    TERMINAL_TASK_STATUSES,
    mark_task_queued,
    record_task_status,
)
from shared.validation import validate_submission_payload

logger = logging.getLogger(__name__)


class QueueUnavailable(RuntimeError):
    """Raised when the async queue is temporarily unavailable."""


class IdempotencyConflict(Exception):
    """Base class: the idempotency key cannot be used for this request right now."""


class IdempotencyKeyMismatch(IdempotencyConflict):
    """The key was already used with a different payload (HTTP 422)."""


class IdempotencyRequestInFlight(IdempotencyConflict):
    """A request with this key is still queued or running (HTTP 409)."""


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
                raise IdempotencyKeyMismatch(
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
                raise IdempotencyKeyMismatch(
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
    resolved_key = _normalize_idempotency_key(idempotency_key)
    if resolved_key:
        _reject_if_claimed_elsewhere(resolved_key, request_fingerprint(validated))
    return _persist_submission(
        validated,
        form_type="sync",
        status="processed",
        idempotency_key=idempotency_key,
    )


def _reject_if_claimed_elsewhere(idempotency_key: str, fingerprint: str) -> None:
    """A sync request must not race an async request that owns the same key."""
    with session_scope() as session:
        claim = session.get(IdempotencyClaim, idempotency_key)
        if claim is None:
            return
        if claim.request_fingerprint != fingerprint:
            raise IdempotencyKeyMismatch(
                "Idempotency key already exists for a different submission."
            )
        if _claim_task_status(session, claim) not in TERMINAL_TASK_STATUSES:
            raise IdempotencyRequestInFlight(
                "A request with this idempotency key is still being processed."
            )


def _claim_task_status(session, claim: IdempotencyClaim) -> str | None:
    task_status = (
        session.query(TaskStatus)
        .filter(TaskStatus.task_id == claim.task_id)
        .one_or_none()
    )
    return None if task_status is None else task_status.status


def enqueue_submission_async(
    first_name: str,
    last_name: str,
    email: str,
    honeypot: str = "",
    *,
    idempotency_key: str | None = None,
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

    resolved_key = _normalize_idempotency_key(idempotency_key)
    if resolved_key:
        task_kwargs["idempotency_key"] = resolved_key
        return _enqueue_with_claim(
            save_async_submission_task, task_kwargs, resolved_key, validated
        )

    try:
        task = save_async_submission_task.delay(**task_kwargs)
    except OperationalError as exc:
        raise QueueUnavailable(
            "Queue is temporarily unavailable. Please try again shortly."
        ) from exc
    try:
        mark_task_queued(task.id)
    except Exception as exc:  # noqa: BLE001 - best-effort status writes
        logger.warning(
            "task status write failed",
            extra={
                "event": "task_status_write_failed",
                "task_id": task.id,
                "status": "queued",
                "error_type": type(exc).__name__,
            },
        )
    return task.id


def _enqueue_with_claim(
    task, task_kwargs: dict, idempotency_key: str, validated
) -> str:
    """Claim the key in the database before enqueueing, following the IETF draft.

    draft-ietf-httpapi-idempotency-key-header: reusing a key with a different
    payload is 422; a request whose original is still outstanding is 409. The
    claim row's primary key is the idempotency key, so of two concurrent first
    requests exactly one inserts it and enqueues; the other is classified.

    Returns the task id that owns the key: a new one, or the original one when
    the same request is replayed after it was processed.
    """
    fingerprint = request_fingerprint(validated)
    task_id = str(uuid.uuid4())

    with session_scope() as session:
        claim = session.get(IdempotencyClaim, idempotency_key)
        if claim is None:
            session.add(
                IdempotencyClaim(
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    task_id=task_id,
                )
            )
            try:
                session.commit()
                claim = None
            except IntegrityError:
                session.rollback()
                claim = session.get(IdempotencyClaim, idempotency_key)
                if claim is None:
                    raise
        if claim is not None:
            existing = _classify_existing_claim(session, claim, fingerprint, task_id)
            if existing is not None:
                return existing

    # Write "queued" before enqueueing so the claim never points at a task with
    # no status row because the worker has not started yet.
    mark_task_queued(task_id)
    try:
        task.apply_async(kwargs=task_kwargs, task_id=task_id)
    except OperationalError as exc:
        record_task_status(task_id, "failed", error_type="QueueUnavailable")
        raise QueueUnavailable(
            "Queue is temporarily unavailable. Please try again shortly."
        ) from exc
    _log_claim_event("idempotency_claim_enqueued", idempotency_key, task_id)
    return task_id


def _classify_existing_claim(
    session, claim, fingerprint: str, new_task_id: str
) -> str | None:
    """Return the task id to report, or None when the key was re-claimed for a retry."""
    if claim.request_fingerprint != fingerprint:
        _log_claim_event(
            "idempotency_key_mismatch", claim.idempotency_key, claim.task_id
        )
        raise IdempotencyKeyMismatch(
            "Idempotency key already exists for a different submission."
        )

    status = _claim_task_status(session, claim)
    if status == "processed":
        _log_claim_event("idempotency_replay", claim.idempotency_key, claim.task_id)
        return claim.task_id
    if status != "failed":
        # Queued, processing, retrying, or no status row yet: still outstanding.
        _log_claim_event("idempotency_in_flight", claim.idempotency_key, claim.task_id)
        raise IdempotencyRequestInFlight(
            "A request with this idempotency key is still being processed."
        )

    # The original attempt failed; the client may retry with the same key.
    # Move the claim to the new task only if nobody else already did.
    moved = session.execute(
        update(IdempotencyClaim)
        .where(
            IdempotencyClaim.idempotency_key == claim.idempotency_key,
            IdempotencyClaim.task_id == claim.task_id,
        )
        .values(task_id=new_task_id)
    )
    session.commit()
    if moved.rowcount != 1:
        raise IdempotencyRequestInFlight(
            "A request with this idempotency key is still being processed."
        )
    return None


def _log_claim_event(event: str, idempotency_key: str, task_id: str) -> None:
    logger.info(
        event,
        extra={"event": event, "idempotency_key": idempotency_key, "task_id": task_id},
    )


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
