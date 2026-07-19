"""An OAuth login must survive a daemon restart, and must not leak forever.

The in-flight PKCE state used to live in a module-level dict in `navig.providers.
connect`, on the stated assumption that "begin + complete hit the same gateway
process". True — until that process restarts. And NAVIG *itself* tells you to
restart it mid-setup (`navig gateway start` after a Lighthouse deploy), so a login
begun in the OS/deck UI would die between opening the browser and pasting the code,
surfacing as the useless "No pending login for that handle (expired or already
used)".

The dict also never pruned: an abandoned login kept its PKCE verifier in memory
forever, long past the 10-minute expiry the flow itself enforces.

Persisting the flow must NOT quietly weaken it — expiry, CSRF-state binding and
single-use are all still enforced. Those invariants are pinned here too.
"""

from __future__ import annotations

import time

import pytest

from navig.providers import claude_oauth
from navig.providers import connect as c
from navig.providers.connect import begin_oauth, complete_oauth
from navig.providers.connection_types import ConnectionValidationError
from navig.providers.connections import ConnectionStore


class FakeVault:
    def __init__(self):
        self.items: dict[str, bytes] = {}

    def put(self, label, data, **_kw):
        self.items[label] = data

    def get_bytes(self, label):
        return self.items.get(label)

    def delete(self, label):
        self.items.pop(label, None)


def _store(tmp_path) -> ConnectionStore:
    return ConnectionStore(tmp_path / "connections.db", vault=FakeVault())


# ── the bug: a restart killed the login ─────────────────────────────────────


async def test_login_survives_a_daemon_restart(tmp_path, monkeypatch):
    """Begin the login, throw the process away, complete it from a FRESH store.

    A brand-new ConnectionStore over the same file is exactly what a restarted
    daemon has: no in-memory state, only what was persisted.
    """
    db = tmp_path / "connections.db"

    started = begin_oauth("claude-max", store=ConnectionStore(db, vault=FakeVault()))

    # ---- daemon restarts here: every bit of in-memory state is gone ----
    restarted = ConnectionStore(db, vault=FakeVault())

    async def fake_exchange(code, flow):
        # The rehydrated flow must carry the real PKCE material through.
        assert flow.code_verifier
        assert flow.state
        return {"access_token": "tok", "refresh_token": "r", "expires_at": None, "scopes": []}

    async def fake_validate(self, *, secret_ref, endpoint=None, model=None):
        from navig.providers.drivers.base import ValidationResult

        return ValidationResult(ok=True, health="healthy", models=[])

    monkeypatch.setattr(claude_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr("navig.providers.drivers.native.NativeDriver.validate", fake_validate)

    conn = await complete_oauth(started["handle"], "code", store=restarted)
    assert conn.template_id == "claude-max"


# ── invariants that persistence must not weaken ─────────────────────────────


async def test_a_handle_is_still_single_use(tmp_path, monkeypatch):
    store = _store(tmp_path)

    async def fake_exchange(code, flow):
        return {"access_token": "tok", "refresh_token": "r", "expires_at": None, "scopes": []}

    async def fake_validate(self, *, secret_ref, endpoint=None, model=None):
        from navig.providers.drivers.base import ValidationResult

        return ValidationResult(ok=True, health="healthy", models=[])

    monkeypatch.setattr(claude_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr("navig.providers.drivers.native.NativeDriver.validate", fake_validate)

    started = begin_oauth("claude-max", store=store)
    await complete_oauth(started["handle"], "code", store=store)

    # Replaying the same handle must fail — the row is consumed on read.
    with pytest.raises(ConnectionValidationError):
        await complete_oauth(started["handle"], "code", store=store)


async def test_expiry_is_still_enforced_after_rehydration(tmp_path):
    """created_at is carried through, so persisting a flow can't extend its life."""
    store = _store(tmp_path)
    started = begin_oauth("claude-max", store=store)

    # Backdate the stored login past STATE_EXPIRY_S (10 min).
    stale = time.time() - (claude_oauth.STATE_EXPIRY_S + 60)
    store.put_pending_oauth(
        started["handle"],
        kind="claude",
        template_id="claude-max",
        state="s",
        code_verifier="v",
        created_at=stale,
    )

    # exchange_code raises on an expired flow — surfaced, not silently accepted.
    with pytest.raises(Exception, match="expired"):
        await complete_oauth(started["handle"], "code", store=store)


def test_begin_oauth_does_not_leak_the_code_verifier_to_callers(tmp_path):
    """`state` may be returned (it's already in the auth URL). The verifier may NOT."""
    store = _store(tmp_path)
    started = begin_oauth("claude-max", store=store)

    assert started["state"]
    assert "code_verifier" not in started
    assert "verifier" not in str(started).lower().replace("code_verifier", "")


# ── the leak: abandoned logins used to live forever ─────────────────────────


def test_abandoned_logins_are_pruned(tmp_path):
    store = _store(tmp_path)

    store.put_pending_oauth(
        "old", kind="claude", template_id="claude-max", state="s",
        code_verifier="v", created_at=time.time() - 10_000,
    )
    store.put_pending_oauth(
        "fresh", kind="claude", template_id="claude-max", state="s",
        code_verifier="v", created_at=time.time(),
    )

    removed = store.prune_pending_oauth(time.time() - claude_oauth.STATE_EXPIRY_S)

    assert removed == 1
    assert store.take_pending_oauth("old") is None, "a stale PKCE verifier outlived its expiry"
    assert store.take_pending_oauth("fresh") is not None


def test_beginning_a_login_sweeps_stale_ones(tmp_path):
    """The prune runs on the way in, so the table can't grow without bound."""
    store = _store(tmp_path)
    store.put_pending_oauth(
        "ancient", kind="claude", template_id="claude-max", state="s",
        code_verifier="v", created_at=time.time() - 10_000,
    )

    begin_oauth("claude-max", store=store)

    assert store.take_pending_oauth("ancient") is None


def test_the_in_memory_map_is_gone(tmp_path):
    """Nothing may reintroduce a module-level dict — that was the whole bug."""
    assert not hasattr(c, "_PENDING_OAUTH")


# ── a failed exchange must not cost the user the whole browser round-trip ────


async def test_a_wrong_code_can_be_retried_without_restarting_the_login(tmp_path, monkeypatch):
    """Fat-fingering the code (or a network blip) used to burn the handle.

    The row was consumed BEFORE the exchange, so any failure meant redoing the
    entire browser login. It's restored on a failed exchange now.
    """
    store = _store(tmp_path)
    started = begin_oauth("claude-max", store=store)

    calls: list[str] = []

    async def flaky_exchange(code, flow):
        calls.append(code)
        if code == "wrong":
            raise RuntimeError("invalid_grant")
        return {"access_token": "tok", "refresh_token": "r", "expires_at": None, "scopes": []}

    async def fake_validate(self, *, secret_ref, endpoint=None, model=None):
        from navig.providers.drivers.base import ValidationResult

        return ValidationResult(ok=True, health="healthy", models=[])

    monkeypatch.setattr(claude_oauth, "exchange_code", flaky_exchange)
    monkeypatch.setattr("navig.providers.drivers.native.NativeDriver.validate", fake_validate)

    with pytest.raises(RuntimeError, match="invalid_grant"):
        await complete_oauth(started["handle"], "wrong", store=store)

    # SAME handle still works — no second trip through the browser.
    conn = await complete_oauth(started["handle"], "right", store=store)
    assert conn.template_id == "claude-max"
    assert calls == ["wrong", "right"]


async def test_a_successful_exchange_still_consumes_the_handle_exactly_once(
    tmp_path, monkeypatch
):
    """Retry-on-failure must not weaken single-use on the success path."""
    store = _store(tmp_path)

    async def fake_exchange(code, flow):
        return {"access_token": "tok", "refresh_token": "r", "expires_at": None, "scopes": []}

    async def fake_validate(self, *, secret_ref, endpoint=None, model=None):
        from navig.providers.drivers.base import ValidationResult

        return ValidationResult(ok=True, health="healthy", models=[])

    monkeypatch.setattr(claude_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr("navig.providers.drivers.native.NativeDriver.validate", fake_validate)

    started = begin_oauth("claude-max", store=store)
    await complete_oauth(started["handle"], "code", store=store)

    with pytest.raises(ConnectionValidationError):
        await complete_oauth(started["handle"], "code", store=store)


async def test_an_expired_flow_is_never_restored(tmp_path):
    """A dead login must stay dead — don't hand back a handle past its expiry."""
    store = _store(tmp_path)
    started = begin_oauth("claude-max", store=store)
    store.put_pending_oauth(
        started["handle"], kind="claude", template_id="claude-max", state="s",
        code_verifier="v", created_at=time.time() - (claude_oauth.STATE_EXPIRY_S + 60),
    )

    with pytest.raises(Exception, match="expired"):
        await complete_oauth(started["handle"], "code", store=store)

    assert store.take_pending_oauth(started["handle"]) is None


# ── diagnostics surface the login count, never the login ─────────────────────


def test_diagnostics_reports_pending_logins_as_a_count_only(tmp_path):
    """`navig doctor` / `navig connect doctor` should show that a login is stuck —
    but the report is meant to be pasteable into a bug report, so it must carry a
    COUNT and never the handle, the state, or the code_verifier."""
    from navig.providers.connect import diagnostics_report

    store = _store(tmp_path)
    store.put_pending_oauth(
        "secret-handle", kind="claude", template_id="claude-max",
        state="secret-state", code_verifier="SUPER-SECRET-VERIFIER",
        created_at=time.time(),
    )

    report = diagnostics_report(store=store)
    blob = str(report)

    assert report["pending_logins"] == 1
    assert "SUPER-SECRET-VERIFIER" not in blob
    assert "secret-handle" not in blob
    assert "secret-state" not in blob


def test_count_pending_oauth_is_zero_on_a_fresh_store(tmp_path):
    assert _store(tmp_path).count_pending_oauth() == 0
