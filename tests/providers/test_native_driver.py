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
        # 5xx is the PROVIDER failing, never the operator's credential.
        ("Provider nvidia returned HTTP 503. Sent body: {...}", HealthState.DEGRADED.value),
        ("Server error '502 Bad Gateway'", HealthState.DEGRADED.value),
        ("HTTP 500 Internal Server Error", HealthState.DEGRADED.value),
        ("Service Unavailable", HealthState.DEGRADED.value),
        ("overloaded_error: Overloaded", HealthState.DEGRADED.value),
        ("Server error '504 Gateway Timeout'", HealthState.UNREACHABLE.value),
        ("some weird error", HealthState.INVALID.value),
    ],
)
def test_error_taxonomy(msg, expected_health):
    health, friendly = parse_test_connection_error(msg)
    assert health == expected_health
    assert friendly  # always a non-empty user-facing message


@pytest.mark.parametrize(
    "msg",
    [
        "Provider nvidia returned HTTP 503. Sent body: {...}",
        "Server error '502 Bad Gateway'",
        "HTTP 500 Internal Server Error",
        "Server error '504 Gateway Timeout'",
        "429 rate limit exceeded",
    ],
)
def test_a_provider_outage_is_never_reported_as_a_bad_credential(msg):
    """INVALID is documented in `_validate_and_store` as "real auth failure only".

    It is not cosmetic: that branch persists ``needs_reauth`` and discards
    ``Capability.INFERENCE``, so a transient 5xx used to take a working provider
    out of routing until the operator re-authenticated a perfectly good key.
    Observed live — `navig connect test` printed
    ``● needs_reauth NVIDIA NIM`` off a single 503.
    """
    health, _ = parse_test_connection_error(msg)
    assert health != HealthState.INVALID.value


@pytest.mark.parametrize(
    "msg",
    ["401 Unauthorized", "Invalid API key", "403 Forbidden", "invalid_api_key"],
)
def test_a_real_auth_failure_is_still_invalid(msg):
    """Anti-over-suppression floor: widening the transient set must not swallow
    the case the re-auth prompt exists for."""
    health, _ = parse_test_connection_error(msg)
    assert health == HealthState.INVALID.value


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
