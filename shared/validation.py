from __future__ import annotations

import re
from typing import Any, Mapping

from email_validator import EmailNotValidError, validate_email


NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z\s'-]{0,99}$")


class ValidationError(ValueError):
    """Raised when incoming submission data is invalid."""


def validate_name(value: str | None, field_name: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValidationError(f"{field_name} is required.")
    if not NAME_PATTERN.match(cleaned):
        raise ValidationError(f"{field_name} contains invalid characters.")
    return cleaned


def validate_email_address(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValidationError("Email is required.")
    try:
        result = validate_email(cleaned, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValidationError("Enter a valid email address.") from exc
    return result.normalized


def validate_honeypot(value: str | None) -> None:
    if (value or "").strip():
        raise ValidationError("Spam detected.")


def validate_submission_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    first_name = validate_name(payload.get("first_name"), "First name")
    last_name = validate_name(payload.get("last_name"), "Last name")
    email = validate_email_address(payload.get("email"))
    validate_honeypot(payload.get("honeypot"))
    return {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
    }
