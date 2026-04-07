from __future__ import annotations

import pytest

from shared.validation import ValidationError, validate_email_address, validate_name, validate_submission_payload


def test_validate_submission_payload_checks_honeypot_first():
    with pytest.raises(ValidationError, match="Spam detected."):
        validate_submission_payload(
            {
                "first_name": "",
                "last_name": "",
                "email": "not-an-email",
                "honeypot": "bot-filled",
            }
        )


def test_validate_name_accepts_common_realistic_names():
    assert validate_name("O'Connor", "First name") == "O'Connor"
    assert validate_name("Jean-Luc", "First name") == "Jean-Luc"


def test_validate_name_rejects_non_ascii_under_current_rules():
    with pytest.raises(ValidationError, match="contains invalid characters"):
        validate_name("José", "First name")


def test_validate_email_address_accepts_254_character_email():
    local = "l" * 64
    domain = f"{'a' * 63}.{'b' * 63}.{'c' * 61}"
    email_254 = f"{local}@{domain}"
    assert len(email_254) == 254
    assert validate_email_address(email_254) == email_254


def test_validate_email_address_rejects_255_character_email():
    too_long = f"{'x' * 243}@example.com"
    assert len(too_long) == 255
    with pytest.raises(ValidationError, match="Email is too long"):
        validate_email_address(too_long)


def test_validate_email_address_rejects_disposable_domain():
    with pytest.raises(ValidationError, match="Disposable email addresses are not allowed"):
        validate_email_address("user@mailinator.com")
