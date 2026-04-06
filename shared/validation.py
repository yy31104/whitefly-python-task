from __future__ import annotations

import re
from typing import Any, Mapping

from email_validator import EmailNotValidError, validate_email


NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z\s'-]*$")
MIN_NAME_LENGTH = 2
MAX_NAME_LENGTH = 50
MAX_EMAIL_LENGTH = 254
DISPOSABLE_EMAIL_DOMAINS = {
    "10minutemail.com",
    "guerrillamail.com",
    "mailinator.com",
    "temp-mail.org",
}


class ValidationError(ValueError):
    """Raised when incoming submission data is invalid."""


def validate_name(value: str | None, field_name: str) -> str:
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        raise ValidationError(f"{field_name} is required.")
    if len(cleaned) < MIN_NAME_LENGTH:
        raise ValidationError(f"{field_name} must be at least {MIN_NAME_LENGTH} characters.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValidationError(f"{field_name} must be at most {MAX_NAME_LENGTH} characters.")
    if not NAME_PATTERN.match(cleaned):
        raise ValidationError(f"{field_name} contains invalid characters.")
    if "--" in cleaned or "''" in cleaned:
        raise ValidationError(f"{field_name} contains invalid punctuation.")
    return cleaned


def validate_email_address(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValidationError("Email is required.")
    if len(cleaned) > MAX_EMAIL_LENGTH:
        raise ValidationError("Email is too long.")
    try:
        result = validate_email(cleaned, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValidationError("Enter a valid email address.") from exc

    normalized = result.normalized
    domain = (result.domain or "").lower()
    if domain in DISPOSABLE_EMAIL_DOMAINS:
        raise ValidationError("Disposable email addresses are not allowed.")
    return normalized


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
