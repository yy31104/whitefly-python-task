from __future__ import annotations

import pytest

from shared.rate_limit import trusted_client_identifier


@pytest.mark.parametrize(
    ("x_real_ip", "x_forwarded_for", "remote_addr", "expected"),
    [
        ("203.0.113.10", "198.51.100.1, 198.51.100.2", "127.0.0.1", "203.0.113.10"),
        ("", "198.51.100.1, 198.51.100.2", "127.0.0.1", "198.51.100.1"),
        (None, None, "127.0.0.1", "127.0.0.1"),
        (None, "   ", "   ", "unknown"),
    ],
)
def test_trusted_client_identifier(
    x_real_ip: str | None,
    x_forwarded_for: str | None,
    remote_addr: str | None,
    expected: str,
):
    assert (
        trusted_client_identifier(
            x_real_ip=x_real_ip,
            x_forwarded_for=x_forwarded_for,
            remote_addr=remote_addr,
        )
        == expected
    )
