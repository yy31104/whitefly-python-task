from __future__ import annotations

from collections.abc import Mapping


SYNC_FORM_FIELDS = ("first_name", "last_name", "email", "honeypot")


def extract_sync_form_data(form_data: Mapping[str, str]) -> dict[str, str]:
    return {field: (form_data.get(field) or "").strip() for field in SYNC_FORM_FIELDS}
