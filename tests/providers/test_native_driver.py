"""Pure-helper tests for the NativeDriver: error taxonomy + loopback detection.
These gate the security-relevant mapping (auth failures vs unreachable vs SSRF-y
local) without needing a network round-trip."""

from __future__ import annotations

import pytest

from navig.providers.connection_types import HealthState
from navig.providers.drivers.native import is_loopback, parse_test_connection_error


@pytest.mark.parametrize(
    "msg,expected_health",
    [
        ("401 Unauthorized", HealthState.INVALID.value),
        ("Invalid API key", HealthState.INVALID.value),
        ("403 Forbidden", HealthState.INVALID.value),
        ("ECONNREFUSED 127.0.0.1:1234", HealthState.UNREACHABLE.value),
        ("fetch failed", HealthState.UNREACHABLE.value),
        ("Request timed out", HealthState.UNREACHABLE.value),
        ("404 model not found", HealthState.DEGRADED.value),
        ("404 Not Found", HealthState.UNREACHABLE.value),
        ("429 rate limit exceeded", HealthState.DEGRADED.value),
        ("some weird error", HealthState.INVALID.value),
    ],
)
def test_error_taxonomy(msg, expected_health):
    health, friendly = parse_test_connection_error(msg)
    assert health == expected_health
    assert friendly  # always a non-empty user-facing message


def test_error_message_truncated_and_no_raw_dump():
    health, friendly = parse_test_connection_error("x" * 1000)
    assert len(friendly) <= 300


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:11434/v1", True),
        ("http://localhost:1234/v1", True),
        ("http://[::1]:8080", True),
        ("https://api.openai.com/v1", False),
        ("http://169.254.169.254/latest/meta-data", False),  # cloud metadata is NOT loopback
        ("", False),
        (None, False),
        ("not a url", False),
    ],
)
def test_loopback_detection(url, expected):
    assert is_loopback(url) is expected
