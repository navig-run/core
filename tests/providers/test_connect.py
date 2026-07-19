"""
Tests for the connect.py orchestration flow (Phase 2) — the single entry point
deck/CLI/onboarding share. Uses an injected FakeDriver + FakeVault so no network
or Node bridge is needed.
"""

from __future__ import annotations

import pytest

from navig.providers import connect as connect_mod
from navig.providers.connect import (
    CONNECTION_TEMPLATES,
    connect_provider,
    disconnect,
    list_connections,
    resolve_default,
    set_default,
    set_workspace_default,
)
from navig.providers.connection_types import (
    AuthState,
    Capability,
    ConnectionValidationError,
    UiState,
)
from navig.providers.connections import ConnectionStore
from navig.providers.drivers.fake import FakeDriver


class FakeVault:
    def __init__(self):
        self.items = {}

    def put(self, label, payload, *, provider=None, **kw):
        self.items[label] = payload
        return "id-" + label

    def get_secret(self, label):
        if label not in self.items:
            raise KeyError(label)
        return self.items[label].decode()

    def delete(self, label):
        return self.items.pop(label, None) is not None


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(tmp_path / "connections.db", vault=FakeVault())


async def test_connect_api_key_creates_ready_connection(store):
    # BYOK api-key providers are saved to the SHARED store and surface as a
    # virtual (live-resolved) connection — not a stored copy.
    conn = await connect_provider(
        "openai-api", api_key="sk-test", store=store, driver=FakeDriver(),
    )
    assert conn.auth_state == AuthState.CONNECTED
    assert conn.connection_id == "configured:openai"
    assert conn.secret_ref is None  # resolved live from the shared store
    assert conn.metadata.get("virtual") is True
    assert Capability.INFERENCE in conn.capabilities
    assert conn.is_routable and conn.ui_state() == UiState.READY
    assert conn.models == ["fake-large", "fake-small"]


async def test_connect_validation_failure_is_needs_reauth_not_routable(store):
    conn = await connect_provider(
        "openai-api", api_key="bad", store=store, driver=FakeDriver(healthy=False),
    )
    assert conn.auth_state == AuthState.NEEDS_REAUTH
    assert Capability.INFERENCE not in conn.capabilities
    assert conn.is_routable is False
    assert conn.ui_state() == UiState.NEEDS_REAUTH


async def test_connect_requires_key(store):
    with pytest.raises(ConnectionValidationError):
        await connect_provider("openai-api", store=store, driver=FakeDriver())


async def test_connect_requires_endpoint(store):
    with pytest.raises(ConnectionValidationError):
        await connect_provider(
            "openai-compat", api_key="k", store=store, driver=FakeDriver()
        )


async def test_unknown_template_raises(store):
    with pytest.raises(ConnectionValidationError):
        await connect_provider("does-not-exist", store=store, driver=FakeDriver())


async def test_local_template_is_keyless(store):
    # Ollama template requires no key; FakeDriver validates healthy.
    conn = await connect_provider("ollama", store=store, driver=FakeDriver())
    assert conn.auth_state == AuthState.CONNECTED
    assert conn.metadata.get("endpoint") == "http://127.0.0.1:11434/v1"


async def test_management_surface_list_default_disconnect(store):
    a = await connect_provider("openai-api", api_key="k1", store=store, driver=FakeDriver())
    b = await connect_provider("anthropic-api", api_key="k2", store=store, driver=FakeDriver())

    rows = list_connections(store=store)
    assert {r["template_id"] for r in rows} == {"openai-api", "anthropic-api"}

    set_default(b.connection_id, store=store)
    assert resolve_default(store=store)["connection_id"] == b.connection_id

    set_workspace_default("space-1", a.connection_id, store=store)
    assert resolve_default("space-1", store=store)["connection_id"] == a.connection_id

    assert disconnect(a.connection_id, store=store) is True
    assert {r["template_id"] for r in list_connections(store=store)} == {"anthropic-api"}


def test_catalog_has_core_templates():
    assert {"openai-api", "anthropic-api", "openai-compat", "ollama", "lmstudio"} <= set(
        CONNECTION_TEMPLATES
    )
