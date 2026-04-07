from __future__ import annotations

from collections.abc import Sequence

from kombu.exceptions import OperationalError

from shared.db import session_scope
from shared.models import Submission
from shared.validation import validate_submission_payload


class QueueUnavailable(RuntimeError):
    """Raised when the async queue is temporarily unavailable."""


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
) -> Submission:
    with session_scope() as session:
        submission = Submission(
            first_name=validated_payload["first_name"],
            last_name=validated_payload["last_name"],
            email=validated_payload["email"],
            form_type=form_type,
            status=status,
        )
        session.add(submission)
        session.commit()
        session.refresh(submission)
        return submission


def create_submission_sync(
    first_name: str,
    last_name: str,
    email: str,
    honeypot: str = "",
) -> Submission:
    validated = validate_submission_data(first_name, last_name, email, honeypot)
    return _persist_submission(validated, form_type="sync", status="processed")


def enqueue_submission_async(
    first_name: str,
    last_name: str,
    email: str,
    honeypot: str = "",
) -> str:
    validated = validate_submission_data(first_name, last_name, email, honeypot)

    # Local import avoids circular imports between task and service modules.
    from worker.tasks import save_async_submission_task

    try:
        task = save_async_submission_task.delay(
            first_name=validated["first_name"],
            last_name=validated["last_name"],
            email=validated["email"],
        )
    except OperationalError as exc:
        raise QueueUnavailable("Queue is temporarily unavailable. Please try again shortly.") from exc
    return task.id


def save_submission_from_worker(
    first_name: str,
    last_name: str,
    email: str,
    *,
    form_type: str = "async",
    status: str = "processed",
) -> Submission:
    validated = validate_submission_data(first_name, last_name, email, honeypot="")
    return _persist_submission(validated, form_type=form_type, status=status)


def list_submissions(limit: int | None = DEFAULT_SUBMISSIONS_LIMIT) -> Sequence[Submission]:
    resolved_limit = DEFAULT_SUBMISSIONS_LIMIT if limit is None else max(1, int(limit))
    with session_scope() as session:
        return (
            session.query(Submission)
            .order_by(Submission.created_at.desc())
            .limit(resolved_limit)
            .all()
        )
