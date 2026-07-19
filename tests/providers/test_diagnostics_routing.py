"""
Phase 5 — routing resolver fallback chain + redacted diagnostics report.
The diagnostics test is a security gate: NO secret value or vault label may
appear anywhere in the report.
"""

from __future__ import annotations

import json

import pytest

from navig.providers.connect import (
    connect_provider,
    diagnostics_report,
    resolve_for,
    set_workspace_default,
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
        return self.items[label].decode()

    def delete(self, label):
        return self.items.pop(label, None) is not None


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(tmp_path / "connections.db", vault=FakeVault())


async def test_resolve_for_fallback_chain(store):
    a = await connect_provider("openai-api", api_key="k1", store=store, driver=FakeDriver())
    b = await connect_provider("anthropic-api", api_key="k2", store=store, driver=FakeDriver())
    set_workspace_default("space-1", b.connection_id, store=store)

    # explicit connection_id wins
    assert resolve_for(connection_id=a.connection_id, store=store)["connection_id"] == a.connection_id
    # workspace default next
    assert resolve_for(workspace_id="space-1", store=store)["connection_id"] == b.connection_id
    # global default fallback (a was first → default)
    assert resolve_for(store=store)["connection_id"] == a.connection_id
    # unknown explicit id falls through to default
    assert resolve_for(connection_id="ghost", store=store)["connection_id"] == a.connection_id


async def test_diagnostics_report_is_fully_redacted(store):
    # connect with a recognizable secret value
    secret = "sk-SUPER-SECRET-VALUE-123"
    await connect_provider(
        "openai-compat", api_key=secret, endpoint="https://api.example.com/v1",
        store=store, driver=FakeDriver(),
    )
    report = diagnostics_report(store=store)
    blob = json.dumps(report)

    # the secret value must NEVER appear
    assert secret not in blob
    # vault label (secret_ref) must be stripped to a boolean marker
    assert "connection/openai-compat/" not in blob
    conn = report["connections"][0]
    assert conn["secret_ref"] is None
    assert conn["has_secret"] is True
    # endpoint path is trimmed to scheme+host
    assert conn["metadata"]["endpoint"] == "https://api.example.com"
    # report still carries useful, non-secret diagnostics
    assert report["default_connection_id"]
    assert report["bridge"]["pi_pinned"] == "0.79.9"
