"""
Phase 3 — external coding-agent detection. Validates that detection probes the
PATH (never reads credential files) and that connecting an external runtime
yields an honest "detected, not routable" connection.
"""

from __future__ import annotations

import pytest

import navig.providers.drivers.external as ext
from navig.providers.connect import connect_provider, detect_external
from navig.providers.connection_types import AuthState, Capability, Driver, UiState
from navig.providers.connections import ConnectionStore
from navig.providers.drivers.external import ExternalDriver


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(tmp_path / "connections.db")


@pytest.fixture
def fake_which(monkeypatch):
    """Pretend only `claude` is installed; nothing else on PATH."""
    def _which(binary):
        return "/usr/local/bin/claude" if binary == "claude" else None
    monkeypatch.setattr(ext.shutil, "which", _which)
    # neutralize the home-dir fallback + configured() existence checks
    monkeypatch.setattr(ext, "_configured", lambda paths: False)
    return _which


def test_detect_finds_only_installed_runtime(fake_which):
    found = ExternalDriver().detect()
    ids = {d["template_id"] for d in found}
    assert ids == {"claude-code"}
    claude = found[0]
    assert claude["installed"] is True
    assert claude["runtime_path"].endswith("claude")
    # detection carries NO secret material
    assert "credentials" not in str(found).lower()


def test_detect_external_orchestrator(fake_which):
    assert {d["template_id"] for d in detect_external()} == {"claude-code"}


async def test_connect_external_is_detected_not_routable(fake_which, store):
    conn = await connect_provider("claude-code", store=store)
    assert conn.driver == Driver.EXTERNAL
    assert conn.auth_state == AuthState.DETECTED_EXTERNAL
    assert Capability.INFERENCE not in conn.capabilities
    assert conn.is_routable is False
    assert conn.ui_state() == UiState.DETECTED_EXTERNALLY
    assert conn.secret_ref is None  # delegated — no secret stored


async def test_connect_external_when_not_installed_is_needs_reauth(monkeypatch, store):
    monkeypatch.setattr(ext.shutil, "which", lambda b: None)
    monkeypatch.setattr(ext, "_configured", lambda paths: False)
    # also block the home-dir fallback
    monkeypatch.setattr(ext, "_which", lambda binary, extra: None)
    conn = await connect_provider("codex", store=store)
    assert conn.auth_state == AuthState.NEEDS_REAUTH
    assert conn.is_routable is False
