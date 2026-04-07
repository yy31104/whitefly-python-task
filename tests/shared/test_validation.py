from __future__ import annotations

import pytest

from shared.validation import ValidationError, validate_submission_payload


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
