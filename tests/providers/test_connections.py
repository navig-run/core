"""
Contract tests for the instance-based connection service (Phase 1) and the
driver contract via the FakeDriver (Phase 0). No Node bridge / network needed.
"""

from __future__ import annotations

import pytest

from navig.providers.connection_types import (
    AuthState,
    Capability,
    Connection,
    ConnectionNotFound,
    Driver,
    HealthState,
    RevisionConflict,
    UiState,
    new_connection_id,
)
from navig.providers.connections import ConnectionStore
from navig.providers.drivers.fake import FakeDriver

# ── fixtures ─────────────────────────────────────────────────────────────────


class FakeVault:
    """Minimal vault stand-in: records puts/deletes, never persists plaintext."""

    def __init__(self):
        self.items: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, label, payload, *, provider=None, **kw):
        self.items[label] = payload
        return "id-" + label

    def delete(self, label):
        self.deleted.append(label)
        return self.items.pop(label, None) is not None


@pytest.fixture
def store(tmp_path):
    vault = FakeVault()
    s = ConnectionStore(tmp_path / "connections.db", vault=vault)
    s._fake_vault = vault  # type: ignore[attr-defined]
    return s


def _mk(name="OpenAI", template="openai-api", driver=Driver.NATIVE, **kw) -> Connection:
    return Connection(
        connection_id=new_connection_id(),
        template_id=template,
        name=name,
        driver=driver,
        auth_state=kw.pop("auth_state", AuthState.CONNECTED),
        health_state=kw.pop("health_state", HealthState.HEALTHY),
        capabilities=kw.pop("capabilities", {Capability.INFERENCE}),
        **kw,
    )


# ── CRUD + default ───────────────────────────────────────────────────────────


def test_create_get_list_and_first_is_default(store):
    c = store.create(_mk())
    assert c.is_default is True  # first connection auto-defaults
    got = store.get(c.connection_id)
    assert got.name == "OpenAI"
    assert [x.connection_id for x in store.list()] == [c.connection_id]


def test_get_missing_raises(store):
    with pytest.raises(ConnectionNotFound):
        store.get("nope")


def test_second_connection_not_default_until_set(store):
    a = store.create(_mk(name="A"))
    b = store.create(_mk(name="B"))
    assert a.is_default and not b.is_default
    store.set_default(b.connection_id)
    assert store.get_default().connection_id == b.connection_id
    assert store.get(a.connection_id).is_default is False


# ── optimistic concurrency ───────────────────────────────────────────────────


def test_update_bumps_revision(store):
    c = store.create(_mk())
    c.name = "Renamed"
    updated = store.update(c, expected_revision=c.revision)
    assert updated.name == "Renamed"
    assert updated.revision == c.revision + 1


def test_stale_update_raises_revision_conflict(store):
    c = store.create(_mk())
    first = store.get(c.connection_id)
    second = store.get(c.connection_id)
    first.name = "A"
    store.update(first, expected_revision=first.revision)
    # second holds the now-stale revision
    second.name = "B"
    with pytest.raises(RevisionConflict):
        store.update(second, expected_revision=second.revision)


# ── workspace resolution ─────────────────────────────────────────────────────


def test_workspace_default_overrides_global(store):
    g = store.create(_mk(name="Global"))
    w = store.create(_mk(name="Ws"))
    store.set_workspace_default("space-1", w.connection_id)
    assert store.resolve_default().connection_id == g.connection_id
    assert store.resolve_default("space-1").connection_id == w.connection_id
    # unknown workspace falls back to global
    assert store.resolve_default("space-x").connection_id == g.connection_id


def test_stale_workspace_override_falls_back(store):
    g = store.create(_mk(name="Global"))
    w = store.create(_mk(name="Ws"))
    store.set_workspace_default("space-1", w.connection_id)
    store.delete(w.connection_id)
    # override now stale → resolves to global, and is cleaned up
    assert store.resolve_default("space-1").connection_id == g.connection_id


# ── safe-remove ──────────────────────────────────────────────────────────────


def test_delete_removes_vault_material_and_promotes_default(store):
    a = store.create(_mk(name="A", secret_ref="connection/a/123"))
    b = store.create(_mk(name="B"))
    store.set_default(a.connection_id)
    store.delete(a.connection_id)
    assert "connection/a/123" in store._fake_vault.deleted  # vault material removed
    with pytest.raises(ConnectionNotFound):
        store.get(a.connection_id)
    # default promoted to the remaining connection
    assert store.get_default().connection_id == b.connection_id


def test_store_secret_uses_vault_and_returns_label(store):
    ref = store.store_secret("openai-api", "sk-secret-value")
    assert ref.startswith("connection/openai-api/")
    assert ref in store._fake_vault.items
    assert store._fake_vault.items[ref] == b"sk-secret-value"


# ── honest UI state ──────────────────────────────────────────────────────────


def test_external_connected_but_not_routable_ui_state():
    c = _mk(
        name="Claude Code",
        driver=Driver.EXTERNAL,
        auth_state=AuthState.CONNECTED,
        capabilities={Capability.AUTH},  # NO inference capability
    )
    assert c.is_routable is False
    assert c.ui_state() == UiState.CONNECTED_EXTERNAL_NOT_ROUTABLE


def test_inference_capable_connected_is_ready():
    c = _mk(capabilities={Capability.INFERENCE}, auth_state=AuthState.CONNECTED)
    assert c.is_routable is True
    assert c.ui_state() == UiState.READY


def test_disabled_overrides_state():
    c = _mk()
    assert c.ui_state(enabled=False) == UiState.DISABLED


# ── driver contract (FakeDriver) ─────────────────────────────────────────────


async def test_fake_driver_detect_and_validate():
    d = FakeDriver(detectable=[{"template_id": "claude-code", "label": "Claude Code"}])
    assert d.detect()[0]["template_id"] == "claude-code"
    res = await d.validate(secret_ref="x")
    assert res.ok and res.health == HealthState.HEALTHY.value
    assert {m.id for m in res.models} == {"fake-large", "fake-small"}


async def test_fake_driver_device_flow_polls_to_connected():
    d = FakeDriver(flow="device_code")
    start = await d.start_auth("github-copilot")
    assert start.flow == "device_code" and start.user_code
    assert (await d.auth_status(start.handle)).state == "authorizing"
    final = await d.auth_status(start.handle)
    assert final.state == "connected" and final.secret_ref


async def test_fake_driver_validation_failure():
    d = FakeDriver(healthy=False)
    res = await d.validate(secret_ref=None)
    assert not res.ok and res.error_code == "validation_error"
