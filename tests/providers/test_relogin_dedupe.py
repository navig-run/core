"""
Regression: `navig connect login claude-max` must be ACCOUNT-AWARE.

The operator runs multiple Claude Max subscriptions (different accounts). So
re-login must UPDATE the connection for the SAME account (matched by the account
uuid the token-exchange returns) and keep DIFFERENT accounts as separate
connections — never blindly delete another account's connection.
"""

from __future__ import annotations

import pytest

from navig.providers import claude_oauth
from navig.providers import connect as c
from navig.providers.claude_oauth import ClaudeOAuthFlow
from navig.providers.connection_types import (
    AuthState,
    Capability,
    Connection,
    Driver,
    HealthState,
    new_connection_id,
)
from navig.providers.connections import ConnectionStore


def _count(store):
    return len([x for x in store.list() if x.template_id == "claude-max"])


def _seed(store, uuid, email, *, default=False, ref=None):
    return store.create(Connection(
        connection_id=new_connection_id(), template_id="claude-max",
        name=f"Claude — {email}", driver=Driver.NATIVE,
        secret_ref=ref or f"connection/claude-max/{uuid}",
        auth_state=AuthState.CONNECTED, health_state=HealthState.HEALTHY,
        capabilities={Capability.INFERENCE}, is_default=default,
        metadata={"account_uuid": uuid, "account_email": email},
    ))


def _pending(store, handle):
    """Seed an in-flight login. It lives in the STORE now (so a login survives a
    daemon restart), not in a module-level dict."""
    flow = ClaudeOAuthFlow(state="s", code_verifier="v")
    store.put_pending_oauth(
        handle,
        kind="claude",
        template_id="claude-max",
        state=flow.state,
        code_verifier=flow.code_verifier,
        created_at=flow.created_at,
    )


def _exchange_returning(uuid, email):
    async def fake(code, flow):
        return {"access_token": "at", "refresh_token": "rt", "expires_at": 9,
                "scopes": [], "account_uuid": uuid, "account_email": email}
    return fake


async def _revalidate_passthrough(cid, store=None):
    return store.get(cid)


async def test_relogin_same_account_updates_not_duplicates(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "c.db")
    _seed(store, "acct-A", "a@x.com", default=True)
    _seed(store, "acct-B", "b@x.com")
    assert _count(store) == 2

    monkeypatch.setattr(claude_oauth, "exchange_code", _exchange_returning("acct-A", "a@x.com"))
    monkeypatch.setattr(c, "revalidate", _revalidate_passthrough)
    _pending(store, "h1")
    conn = await c.complete_oauth("h1", "code#s", store=store)

    assert _count(store) == 2                                   # A updated, B untouched
    assert (conn.metadata or {}).get("account_uuid") == "acct-A"
    assert any((x.metadata or {}).get("account_uuid") == "acct-B" for x in store.list())


async def test_relogin_new_account_creates_separate(tmp_path, monkeypatch):
    store = ConnectionStore(tmp_path / "c.db")
    _seed(store, "acct-A", "a@x.com", default=True)
    assert _count(store) == 1

    monkeypatch.setattr(claude_oauth, "exchange_code", _exchange_returning("acct-C", "c@x.com"))

    async def fake_connect_provider(template_id, *, name=None, store=None, existing_secret_ref=None):
        cid = store.create(Connection(
            connection_id=new_connection_id(), template_id=template_id, name=name or "Claude",
            driver=Driver.NATIVE, secret_ref=existing_secret_ref, auth_state=AuthState.CONNECTED,
            health_state=HealthState.HEALTHY, capabilities={Capability.INFERENCE},
        )).connection_id
        return store.get(cid)

    monkeypatch.setattr(c, "connect_provider", fake_connect_provider)
    _pending(store, "h2")
    conn = await c.complete_oauth("h2", "code#s", store=store)

    assert _count(store) == 2                                   # A + C, both kept
    assert (conn.metadata or {}).get("account_email") == "c@x.com"


async def test_single_account_no_identity_updates(tmp_path, monkeypatch):
    # No account uuid returned + exactly one existing + no --name → update (convenience).
    store = ConnectionStore(tmp_path / "c.db")
    store.create(Connection(
        connection_id=new_connection_id(), template_id="claude-max", name="Claude",
        driver=Driver.NATIVE, secret_ref="connection/claude-max/old",
        auth_state=AuthState.CONNECTED, health_state=HealthState.HEALTHY,
        capabilities={Capability.INFERENCE}, is_default=True,
    ))

    async def fake(code, flow):
        return {"access_token": "at", "refresh_token": "rt", "expires_at": 9, "scopes": []}

    monkeypatch.setattr(claude_oauth, "exchange_code", fake)
    monkeypatch.setattr(c, "revalidate", _revalidate_passthrough)
    _pending(store, "h3")
    await c.complete_oauth("h3", "code#s", store=store)
    assert _count(store) == 1                                   # updated, not duplicated
