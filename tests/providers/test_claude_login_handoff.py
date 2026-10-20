"""NAVIG OS hands its Claude login to the brain; the brain is then its only refresher.

A refresh response can carry a new refresh token that retires the old one, so two
apps refreshing one login sign each other out. The hand-off therefore moves the
refresh token to the brain, and the OS borrows access tokens through the
local-only lease. These tests pin:

* the OS bundle shape (camelCase, ``expiresAt`` in MILLISECONDS) normalises to
  core's (``expires_at`` in seconds). Read as seconds, a ms value would never
  expire, so the token would never be refreshed;
* the lease refreshes a stale token once, under the shared lock, stores the
  rotated bundle, and never returns the refresh token;
* both routes refuse anything that did not originate on this machine,
  including a request Lighthouse relays in from 127.0.0.1.
No network: the token endpoint and validation are monkeypatched.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from navig.providers import claude_oauth
from navig.providers.connect import (
    _normalise_claude_bundle,
    import_claude_oauth,
    lease_claude_access_token,
)
from navig.providers.connection_types import ConnectionValidationError
from navig.providers.connections import ConnectionStore
from navig.providers.drivers.base import ValidationResult


class FakeVault:
    def __init__(self):
        self.items: dict[str, bytes] = {}

    def put(self, label, payload, *, provider=None, **kw):
        self.items[label] = payload if isinstance(payload, bytes) else payload.encode()
        return "id-" + label

    def get_bytes(self, label):
        return self.items[label]

    def get_secret(self, label):
        return self.items[label].decode()

    def delete(self, label):
        return self.items.pop(label, None) is not None


@pytest.fixture
def store(tmp_path, monkeypatch):
    async def fake_validate(self, *, secret_ref, endpoint=None, model=None):
        return ValidationResult(ok=True, health="healthy", models=[])

    monkeypatch.setattr("navig.providers.drivers.native.NativeDriver.validate", fake_validate)
    return ConnectionStore(tmp_path / "connections.db", vault=FakeVault())


def _bundle(store, conn):
    return json.loads(store._vault.items[conn.secret_ref].decode())


# ── normalisation ────────────────────────────────────────────────────────────


def test_os_bundle_in_milliseconds_becomes_seconds():
    out = _normalise_claude_bundle(
        {"accessToken": "a", "refreshToken": "r", "expiresAt": 1_800_000_000_000,
         "accountEmail": "me@example.com"}
    )
    assert out["access_token"] == "a" and out["refresh_token"] == "r"
    assert out["expires_at"] == 1_800_000_000
    assert out["account_email"] == "me@example.com"


def test_core_bundle_in_seconds_is_kept():
    out = _normalise_claude_bundle({"access_token": "a", "refresh_token": "r", "expires_at": 1_800_000_000})
    assert out["expires_at"] == 1_800_000_000


@pytest.mark.parametrize("bad", [{}, {"accessToken": "a"}, {"refreshToken": "r"}, "nope"])
def test_a_bundle_without_both_tokens_is_refused(bad):
    with pytest.raises(ConnectionValidationError):
        _normalise_claude_bundle(bad)  # type: ignore[arg-type]


# ── import + lease ───────────────────────────────────────────────────────────


async def test_import_creates_the_claude_max_connection(store):
    conn = await import_claude_oauth(
        {"accessToken": "a", "refreshToken": "r", "expiresAt": (time.time() + 3600) * 1000},
        store=store,
    )
    assert conn.template_id == "claude-max" and conn.is_routable
    assert _bundle(store, conn)["refresh_token"] == "r"


async def test_lease_returns_a_fresh_token_without_refreshing(store, monkeypatch):
    async def must_not_refresh(rt):
        raise AssertionError("a fresh token must not be refreshed")

    monkeypatch.setattr(claude_oauth, "refresh_tokens", must_not_refresh)
    await import_claude_oauth(
        {"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 3600}, store=store
    )
    lease = await lease_claude_access_token(store=store)
    assert lease["access_token"] == "a"
    assert "refresh_token" not in lease


async def test_lease_refreshes_a_stale_token_once_and_stores_the_rotation(store, monkeypatch):
    calls: list[str] = []

    async def fake_refresh(rt):
        calls.append(rt)
        await asyncio.sleep(0.01)  # let a concurrent caller reach the lock
        return {"access_token": "a2", "refresh_token": "r2", "expires_at": int(time.time()) + 3600}

    monkeypatch.setattr(claude_oauth, "refresh_tokens", fake_refresh)
    conn = await import_claude_oauth(
        {"access_token": "a1", "refresh_token": "r1", "expires_at": time.time() + 10,
         "account_email": "me@example.com"},
        store=store,
    )

    a, b = await asyncio.gather(
        lease_claude_access_token(store=store), lease_claude_access_token(store=store)
    )

    assert calls == ["r1"], "two concurrent leases must refresh ONCE (rotation would kill one)"
    assert a["access_token"] == b["access_token"] == "a2"
    stored = _bundle(store, conn)
    assert stored["refresh_token"] == "r2"
    assert stored["account_email"] == "me@example.com", "a refresh must not drop identity"


async def test_lease_without_a_login_says_so(store):
    with pytest.raises(ConnectionValidationError):
        await lease_claude_access_token(store=store)


# ── routes are local-only ────────────────────────────────────────────────────


class _Req:
    def __init__(self, headers=None, remote="127.0.0.1"):
        self.headers = headers or {}
        self.remote = remote


@pytest.mark.parametrize(
    "req",
    [
        _Req(remote="203.0.113.9"),
        _Req({"X-Navig-Via": "lighthouse"}),  # relayed in, from 127.0.0.1
        _Req({"CF-Ray": "abc", "CF-Connecting-IP": "203.0.113.9"}),
    ],
)
async def test_routes_refuse_anything_not_from_this_machine(req):
    from navig.gateway.deck.routes import providers

    for handler in (providers.handle_deck_providers_claude_token,
                    providers.handle_deck_providers_claude_import):
        resp = await handler(req)
        assert resp.status == 403
        assert json.loads(resp.body)["code"] == "local_only"


def test_routes_are_registered():
    import inspect

    from navig.gateway import deck

    src = inspect.getsource(deck)
    assert '"/api/deck/providers/claude-max/import"' in src
    assert '"/api/deck/providers/claude-max/token"' in src
