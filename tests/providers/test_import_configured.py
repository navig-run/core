"""
Auto-sync: providers configured in the SHARED store (env/vault/auth-profiles,
the same path `navig ai providers` uses) appear automatically as virtual
connections — no import step. `_resolve_auth` is mocked so no real keys are read.
"""

from __future__ import annotations

import pytest

from navig.providers import connect as connect_mod
from navig.providers.connect import (
    VIRTUAL_PREFIX,
    connect_provider,
    disconnect,
    list_connections,
    resolve_default,
    set_default,
)
from navig.providers.connections import ConnectionStore
from navig.providers.drivers.base import ValidationResult


class FakeVault:
    def __init__(self):
        self.items = {}

    def put(self, label, payload, *, provider=None, **kw):
        self.items[label] = payload
        return "id-" + label

    def get_secret(self, label):
        return self.items[label].decode()

    def delete(self, label):
        return self.items.pop(label, None) is not None


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(tmp_path / "connections.db", vault=FakeVault())


@pytest.fixture
def configured(monkeypatch):
    state = {"openai": ("sk-openai", "vault:openai"), "xai": ("xai-key", "vault:xai")}
    monkeypatch.setattr(connect_mod, "_resolve_auth",
                        lambda pid: state.get(pid, (None, None)))
    return state


def test_configured_providers_auto_appear_no_import(configured, store):
    rows = list_connections(store=store)
    by_template = {r["template_id"]: r for r in rows}
    assert "openai-api" in by_template and "xai" in by_template
    oa = by_template["openai-api"]
    assert oa["connection_id"] == VIRTUAL_PREFIX + "openai"
    assert oa["is_routable"] and oa["ui_state"] == "ready"
    assert oa["secret_ref"] is None  # resolved live, not copied
    assert oa["metadata"]["virtual"] is True


def test_removing_shared_key_removes_the_connection(configured, store, monkeypatch):
    removed = []
    monkeypatch.setattr(connect_mod, "_remove_shared_key", lambda pid: removed.append(pid))
    assert disconnect(VIRTUAL_PREFIX + "xai", store=store) is True
    assert removed == ["xai"]


def test_set_default_on_virtual_connection(configured, store):
    set_default(VIRTUAL_PREFIX + "xai", store=store)
    assert resolve_default(store=store)["connection_id"] == VIRTUAL_PREFIX + "xai"
    rows = {r["connection_id"]: r for r in list_connections(store=store)}
    assert rows[VIRTUAL_PREFIX + "xai"]["is_default"] is True


async def test_connect_add_writes_to_shared_store_and_returns_virtual(configured, store, monkeypatch):
    """`navig connect add groq` saves via the shared path (so `navig ai` sees it)
    and returns a virtual connection — not a separate stored copy."""
    saved = {}
    monkeypatch.setattr(connect_mod, "_save_shared_key",
                        lambda pid, key: saved.__setitem__(pid, key))

    async def fake_validate(self, *, secret_ref, endpoint=None, model=None):
        return ValidationResult(ok=True, health="healthy", models=[])

    monkeypatch.setattr("navig.providers.drivers.native.NativeDriver.validate", fake_validate)

    conn = await connect_provider("groq", api_key="gsk_test", store=store)
    assert saved == {"groq": "gsk_test"}              # written to shared store
    assert conn.connection_id == VIRTUAL_PREFIX + "groq"  # virtual, not stored
    assert conn.secret_ref is None
    assert store.list() == []                         # nothing stored separately
