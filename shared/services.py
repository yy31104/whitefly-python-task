from __future__ import annotations

from collections.abc import Sequence

from shared.db import session_scope
from shared.models import Submission
from shared.validation import validate_submission_payload


def create_submission_sync(
    first_name: str,
    last_name: str,
    email: str,
    honeypot: str = "",
) -> Submission:
    validated = validate_submission_payload(
        {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "honeypot": honeypot,
        }
    )

    with session_scope() as session:
        submission = Submission(
            first_name=validated["first_name"],
            last_name=validated["last_name"],
            email=validated["email"],
            form_type="sync",
            status="processed",
        )
        session.add(submission)
        session.commit()
        session.refresh(submission)
        return submission


def list_submissions() -> Sequence[Submission]:
    with session_scope() as session:
        return session.query(Submission).order_by(Submission.created_at.desc()).all()
