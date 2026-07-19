"""
Real-inference smoke (env-gated, CI-safe).

When a live key is present (NAVIG_SMOKE_OPENAI_KEY or OPENAI_API_KEY), this
connects an `openai-api` connection through the full stack — store_secret →
NativeDriver.validate → a real 1-token completion against the provider — and
asserts the connection comes back routable. Skipped automatically otherwise so
the suite stays green offline.

Run it explicitly:
    NAVIG_SMOKE_OPENAI_KEY=sk-... python -m pytest tests/providers/test_inference_smoke.py -q
"""

from __future__ import annotations

import os

import pytest

from navig.providers.connect import connect_provider
from navig.providers.connection_types import AuthState, Capability
from navig.providers.connections import ConnectionStore

_KEY = os.environ.get("NAVIG_SMOKE_OPENAI_KEY") or os.environ.get("OPENAI_API_KEY")
_MODEL = os.environ.get("NAVIG_SMOKE_OPENAI_MODEL", "gpt-4o-mini")

pytestmark = pytest.mark.skipif(not _KEY, reason="no live OpenAI key in env")


class _FakeVault:
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


async def test_real_openai_inference(tmp_path):
    store = ConnectionStore(tmp_path / "connections.db", vault=_FakeVault())
    conn = await connect_provider(
        "openai-api", api_key=_KEY, model=_MODEL, store=store,
    )
    assert conn.auth_state == AuthState.CONNECTED, conn.metadata
    assert Capability.INFERENCE in conn.capabilities
    assert conn.is_routable is True
    # secret is in the vault as a ref, never on the record in plaintext
    assert conn.secret_ref and conn.secret_ref != _KEY
