"""Shared-BYOK key safety: connecting a provider must never destroy a working key.

A plain API-key provider ("shared BYOK") does NOT get its own copy of the secret —
`connect_provider` writes it into the SHARED auth store that `navig ai` also reads,
so the two stay in sync. That sharing is the point, but it means a failed
`navig connect add` is writing over a key the user may be relying on RIGHT NOW,
with no revision history.

Two holes in that rollback, both fixed here:

  * The key is overwritten BEFORE validation. If validation *raised* (a network
    blip, a driver crash) the exception propagated with the old key already gone
    and nothing put back — silent, permanent destruction of a working key.

  * `resolve_auth` checks auth profiles first and only THEN the environment. So a
    key that resolved from `$OPENAI_API_KEY` was never in the profile store, and
    "restoring" it there persisted an environment secret to disk and permanently
    shadowed the env var (profiles win). The correct undo is to drop the profile
    entry we created — exactly what `disconnect()` already reasons about.
"""

from __future__ import annotations

import pytest

from navig.providers import connect as _connect
from navig.providers.connect import connect_provider
from navig.providers.connection_types import AuthState, HealthState
from navig.providers.connections import ConnectionStore
from navig.providers.drivers.base import ValidationResult
from navig.providers.drivers.fake import FakeDriver


class _RaisingDriver(FakeDriver):
    """Validation blows up mid-flight (transport died, driver bug)."""

    async def validate(self, *, secret_ref, endpoint=None, model=None):
        raise ConnectionError("network went away mid-validate")


class _TransientDriver(FakeDriver):
    """A 429/timeout — does NOT prove the credential is bad."""

    async def validate(self, *, secret_ref, endpoint=None, model=None):
        return ValidationResult(
            ok=False,
            health=HealthState.DEGRADED.value,
            error_code="rate_limited",
            error_message="429 Too Many Requests",
        )


def _store(tmp_path) -> ConnectionStore:
    return ConnectionStore(tmp_path / "c.db")


# ── A raised validation must not destroy the previous key ────────────────────


async def test_transport_error_restores_the_previous_key(tmp_path, isolate_shared_auth):
    """The bug: the key was overwritten, validate() raised, nothing was restored."""
    shared = isolate_shared_auth
    shared["openai"] = "sk-good-existing"  # what `navig ai` is using right now

    with pytest.raises(ConnectionError, match="network went away"):
        await connect_provider(
            "openai-api", api_key="sk-new", store=_store(tmp_path), driver=_RaisingDriver()
        )

    assert shared["openai"] == "sk-good-existing", (
        "a transport error during connect destroyed the user's working `navig ai` key"
    )


async def test_transport_error_with_no_previous_key_leaves_nothing_behind(
    tmp_path, isolate_shared_auth
):
    """Nothing was configured before, so nothing should be configured after a failure."""
    shared = isolate_shared_auth

    with pytest.raises(ConnectionError):
        await connect_provider(
            "openai-api", api_key="sk-new", store=_store(tmp_path), driver=_RaisingDriver()
        )

    assert "openai" not in shared, "an unvalidated key was left in the shared store"


# ── An env-provided key must never be persisted to disk by the rollback ──────


async def test_rollback_does_not_persist_an_env_key_into_the_profile_store(
    tmp_path, isolate_shared_auth, monkeypatch
):
    """The key lives ONLY in $OPENAI_API_KEY — there is no profile entry to restore.

    Writing it back into the profile store would persist an environment secret to
    disk AND permanently shadow the env var (resolve_auth prefers profiles).
    """
    shared = isolate_shared_auth
    monkeypatch.setattr(
        _connect, "_resolve_auth", lambda _pid: ("sk-from-env", "env:OPENAI_API_KEY")
    )

    conn = await connect_provider(
        "openai-api", api_key="sk-bad", store=_store(tmp_path), driver=FakeDriver(healthy=False)
    )

    assert conn.auth_state == AuthState.NEEDS_REAUTH
    assert "openai" not in shared, (
        "rollback wrote an ENV-provided secret into the on-disk profile store"
    )


async def test_transport_error_with_an_env_key_also_leaves_the_store_clean(
    tmp_path, isolate_shared_auth, monkeypatch
):
    shared = isolate_shared_auth
    monkeypatch.setattr(
        _connect, "_resolve_auth", lambda _pid: ("sk-from-env", "env:OPENAI_API_KEY")
    )

    with pytest.raises(ConnectionError):
        await connect_provider(
            "openai-api", api_key="sk-new", store=_store(tmp_path), driver=_RaisingDriver()
        )

    assert "openai" not in shared


# ── A transient failure deliberately KEEPS the new key ───────────────────────


async def test_transient_failure_keeps_the_new_key(tmp_path, isolate_shared_auth):
    """429/timeout does not prove the key is bad — and the virtual connection
    resolves LIVE from the shared store, so rolling back while still reporting
    CONNECTED would make the returned connection contradict the store."""
    shared = isolate_shared_auth
    shared["openai"] = "sk-old"

    conn = await connect_provider(
        "openai-api", api_key="sk-new", store=_store(tmp_path), driver=_TransientDriver()
    )

    assert conn.auth_state == AuthState.CONNECTED
    assert conn.health_state == HealthState.DEGRADED
    assert shared["openai"] == "sk-new", "the store must agree with the connection we returned"


# ── A failing rollback must not mask the failure that triggered it ───────────


async def test_a_failing_rollback_does_not_mask_the_original_error(
    tmp_path, isolate_shared_auth, monkeypatch
):
    def _boom(*_a, **_k):
        raise OSError("vault is locked")

    monkeypatch.setattr(_connect, "_remove_shared_key", _boom)

    # The ORIGINAL transport error must surface — not the rollback's OSError.
    with pytest.raises(ConnectionError, match="network went away"):
        await connect_provider(
            "openai-api", api_key="sk-new", store=_store(tmp_path), driver=_RaisingDriver()
        )


# ── `navig connect test` must actually TEST a BYOK connection ────────────────
#
# A virtual ("configured elsewhere") connection is SYNTHESISED as connected/healthy
# purely because a key string exists — it is never proven. revalidate() used to
# return that record untouched, so `navig connect test configured:openai` was a
# guaranteed green light even for a garbage key: the one command whose entire job is
# to tell you the truth. "Needs no persistence" (true — there's no row) had been
# conflated with "needs no validation" (false).


@pytest.fixture()
def configured_openai(isolate_shared_auth, monkeypatch):
    """Make `openai` look configured-elsewhere, so it surfaces as a virtual conn."""
    shared = isolate_shared_auth
    shared["openai"] = "sk-whatever"
    return shared


async def test_revalidate_actually_probes_a_virtual_connection(
    tmp_path, configured_openai, monkeypatch
):
    """A bad BYOK key must come back NEEDS_REAUTH — not a synthesized green."""
    from navig.providers import connect as _c

    store = _store(tmp_path)
    monkeypatch.setattr(_c, "get_driver", lambda _t, _s: FakeDriver(healthy=False))

    conn = await _c.revalidate("configured:openai", store=store)

    assert conn.auth_state == AuthState.NEEDS_REAUTH, (
        "`navig connect test` reported a green light without probing the provider"
    )
    assert conn.is_routable is False


async def test_revalidate_reports_a_working_virtual_connection_as_ready(
    tmp_path, configured_openai, monkeypatch
):
    from navig.providers import connect as _c

    store = _store(tmp_path)
    monkeypatch.setattr(_c, "get_driver", lambda _t, _s: FakeDriver(healthy=True))

    conn = await _c.revalidate("configured:openai", store=store)

    assert conn.auth_state == AuthState.CONNECTED
    assert conn.is_routable is True
    assert conn.connection_id == "configured:openai"  # still virtual — no row created


async def test_revalidating_a_virtual_connection_creates_no_stored_row(
    tmp_path, configured_openai, monkeypatch
):
    """It has no row by design — validating it must not accidentally create one."""
    from navig.providers import connect as _c

    store = _store(tmp_path)
    monkeypatch.setattr(_c, "get_driver", lambda _t, _s: FakeDriver(healthy=True))

    await _c.revalidate("configured:openai", store=store)

    assert list(store.list()) == []
