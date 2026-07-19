"""
Regression tests for connection hardening:
  * endpoint_guard: LOCAL templates (allow_loopback) must permit ONLY loopback,
    not arbitrary LAN hosts (SSRF).
  * disconnect(): a virtual connection whose key survives removal (env-backed or
    vault-backed) must RAISE rather than report a false success.
"""

from __future__ import annotations

import pytest

from navig.providers import connect as c
from navig.providers.connection_types import ConnectionValidationError
from navig.providers.connections import ConnectionStore
from navig.providers.endpoint_guard import assert_safe_endpoint

# ── SSRF: allow_loopback is loopback-only ────────────────────────────────────


def test_allow_loopback_permits_loopback():
    assert_safe_endpoint("http://127.0.0.1:11434/v1", allow_loopback=True)  # no raise
    assert_safe_endpoint("http://[::1]:1234/v1", allow_loopback=True)


@pytest.mark.parametrize("lan", [
    "http://10.0.0.5:8080",
    "http://192.168.1.5",
    "http://172.16.0.9/v1",
])
def test_allow_loopback_blocks_lan(lan):
    with pytest.raises(ConnectionValidationError):
        assert_safe_endpoint(lan, allow_loopback=True)


def test_metadata_blocked_regardless():
    with pytest.raises(ConnectionValidationError):
        assert_safe_endpoint("http://169.254.169.254", allow_private=True)


def test_allow_private_still_permits_lan():
    assert_safe_endpoint("http://10.0.0.5", allow_private=True)  # broad opt-in


# ── disconnect honesty ───────────────────────────────────────────────────────


def test_disconnect_env_backed_raises(monkeypatch, tmp_path):
    # key still resolves after removal (env var) → must raise, not lie.
    monkeypatch.setattr(c, "_resolve_auth", lambda pid: ("sk-env", "env:OPENAI_API_KEY"))
    monkeypatch.setattr(c, "_remove_shared_key", lambda pid: None)
    store = ConnectionStore(tmp_path / "c.db")
    with pytest.raises(ConnectionValidationError):
        c.disconnect("configured:openai", store=store)


def test_disconnect_profile_backed_succeeds(monkeypatch, tmp_path):
    shared = {"openai": "sk-profile"}
    monkeypatch.setattr(
        c, "_resolve_auth",
        lambda pid: (shared.get(pid), "profile:x") if pid in shared else (None, "not_found"),
    )
    monkeypatch.setattr(c, "_remove_shared_key", lambda pid: shared.pop(pid, None))
    store = ConnectionStore(tmp_path / "c.db")
    assert c.disconnect("configured:openai", store=store) is True
    assert "openai" not in shared  # actually removed
