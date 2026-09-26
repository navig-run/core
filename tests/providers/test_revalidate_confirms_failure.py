"""A failure is confirmed before it becomes durable state.

`revalidate()` is the single point where a verdict is PERSISTED, and an INVALID
one writes `needs_reauth` and discards `Capability.INFERENCE` — taking a working
provider out of routing until the operator re-authenticates a credential that
was never the problem.

One call is not enough evidence for that. Measured 2026-09-08: NVIDIA answered
HTTP 404 for `nvidia/nemotron-3-super-120b-a12b` during `navig connect test`,
then 200 five times in a row for the same id moments later — the verdict flipped
between "unhealthy" and "needs_reauth" run to run for a healthy provider.

Same principle as `probe_model` re-verifying a 404 before calling a model dead:
the destructive verdict has to be earned.
"""

from __future__ import annotations

import pytest

from navig.providers import connect as connect_mod
from navig.providers.connect import connect_provider, revalidate
from navig.providers.connection_types import AuthState, Capability, HealthState
from navig.providers.connections import ConnectionStore
from navig.providers.drivers.base import ValidationResult
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


class CountingDriver(FakeDriver):
    """Returns each queued outcome in turn, then repeats the last."""

    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = outcomes
        self.calls = 0

    async def validate(self, **_kw):
        self.calls += 1
        return self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]


def _ok() -> ValidationResult:
    return ValidationResult(ok=True, health=HealthState.HEALTHY.value)


def _fail() -> ValidationResult:
    return ValidationResult(
        ok=False, health=HealthState.INVALID.value, error_message="transient 404"
    )


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(tmp_path / "connections.db", vault=FakeVault())


@pytest.fixture
async def connection(store):
    return await connect_provider(
        "openai-api", api_key="sk-test", store=store, driver=FakeDriver()
    )


def _install(monkeypatch, outcomes) -> CountingDriver:
    drv = CountingDriver(outcomes)
    monkeypatch.setattr(connect_mod, "get_driver", lambda *_a, **_kw: drv)
    return drv


async def test_a_one_off_failure_is_re_checked_before_it_is_believed(
    store, connection, monkeypatch
):
    """The measured case: fails once, healthy immediately after."""
    drv = _install(monkeypatch, [_fail(), _ok()])

    conn = await revalidate(connection.connection_id, store=store)

    assert drv.calls == 2, "a failing validation must be confirmed, not persisted blind"
    assert conn.auth_state == AuthState.CONNECTED, "a healthy provider was demoted"
    assert Capability.INFERENCE in conn.capabilities, "inference was dropped on a blip"


async def test_a_persistent_failure_is_still_recorded(store, connection, monkeypatch):
    """Anti-over-suppression floor: a genuinely bad credential must still be caught."""
    drv = _install(monkeypatch, [_fail()])

    conn = await revalidate(connection.connection_id, store=store)

    assert drv.calls == 2, "the failure should be confirmed once"
    assert conn.auth_state == AuthState.NEEDS_REAUTH
    assert Capability.INFERENCE not in conn.capabilities


async def test_a_healthy_provider_is_not_validated_twice(store, connection, monkeypatch):
    """The confirmation must cost nothing on the happy path."""
    drv = _install(monkeypatch, [_ok()])

    conn = await revalidate(connection.connection_id, store=store)

    assert drv.calls == 1
    assert conn.auth_state == AuthState.CONNECTED


def test_both_revalidate_branches_confirm_a_failure():
    """Structural guard for the branch the tests above cannot reach.

    `revalidate()` has TWO paths — virtual (no stored row, verdict only reported)
    and stored (verdict PERSISTED, and INVALID drops INFERENCE). The tests above
    drive the virtual one; a stored connection needs a real OAuth template. This
    pins that neither branch loses its confirmation.

    ⚠ The first fix landed on the stored branch only, and `navig connect test`
    kept mis-reporting NVIDIA because NVIDIA is virtual — the tests above caught
    that by counting real driver calls.
    """
    import inspect

    src = inspect.getsource(connect_mod.revalidate)

    assert src.count("await drv.validate(") == 4, (
        "expected two validate calls per branch (initial + confirmation); "
        "a branch has lost its confirmation"
    )
    assert src.count("if not result.ok:") == 2, "each branch must guard its retry"


# ── connect_provider: the first-time flow, and the most destructive verdict ──
#
# On INVALID, `connect_provider` marks needs_reauth, drops INFERENCE, and for a
# shared key ROLLS BACK the key the user just typed — silently replacing their
# input with the previous one and reporting it invalid. The driver's unknown-error
# fallthrough is INVALID, so one unclassified blip used to discard a valid key.
#
# The shared-key seam is patched to an in-memory dict: hermetic by construction
# (never the real auth profile store), and it lets the ROLLBACK itself be
# asserted rather than inferred from auth_state.


@pytest.fixture
def shared_keys(monkeypatch):
    keys = {"openai": "sk-previous-working-key"}
    monkeypatch.setattr(connect_mod, "_resolve_auth", lambda pid: (keys.get(pid), "profile"))
    monkeypatch.setattr(connect_mod, "_save_shared_key", lambda pid, k: keys.__setitem__(pid, k))
    monkeypatch.setattr(connect_mod, "_remove_shared_key", lambda pid: keys.pop(pid, None))
    return keys


async def test_connect_confirms_a_failure_before_rolling_the_key_back(store, shared_keys):
    """Fails once, healthy on the confirming call → the NEW key stays."""
    drv = CountingDriver([_fail(), _ok()])

    conn = await connect_provider("openai-api", api_key="sk-new", store=store, driver=drv)

    assert drv.calls == 2, "a failing first validation must be confirmed"
    assert conn.auth_state == AuthState.CONNECTED, "a valid key was reported invalid"
    assert shared_keys["openai"] == "sk-new", "the user's key was rolled back on a blip"


async def test_connect_still_rejects_and_rolls_back_a_genuinely_bad_key(store, shared_keys):
    """Anti-over-suppression floor: two failures IS a bad key — record it AND
    restore the previous working key, exactly as before."""
    drv = CountingDriver([_fail()])

    conn = await connect_provider("openai-api", api_key="sk-bad", store=store, driver=drv)

    assert drv.calls == 2
    assert conn.auth_state == AuthState.NEEDS_REAUTH
    assert Capability.INFERENCE not in conn.capabilities
    assert shared_keys["openai"] == "sk-previous-working-key", "a bad key was not rolled back"


async def test_connect_validates_a_good_key_exactly_once(store, shared_keys):
    drv = CountingDriver([_ok()])

    await connect_provider("openai-api", api_key="sk-good", store=store, driver=drv)

    assert drv.calls == 1
    assert shared_keys["openai"] == "sk-good"


def test_all_three_verdict_sites_confirm_before_persisting():
    """The rule has to hold at EVERY site that writes a destructive verdict, or
    the one that does not becomes the way it gets in. Three exist today."""
    import inspect

    src_connect = inspect.getsource(connect_mod.connect_provider)
    src_reval = inspect.getsource(connect_mod.revalidate)

    assert src_connect.count("await drv.validate(") == 2, "connect_provider lost its confirmation"
    assert src_reval.count("await drv.validate(") == 4, "a revalidate branch lost its confirmation"


# ── a failed rollback must be visible, not just logged ───────────────────


def test_a_failed_key_rollback_records_an_incident(monkeypatch):
    """`_restore_shared_key` is a data-RECOVERY path: it puts the user's previous
    working key back after a bad `connect add`. When IT fails, the previous key is
    gone and an unverified one is in its place. Under the daemon a log line
    reaches nobody; an incident reaches `navig doctor` and the notify path."""
    from navig.core import incidents

    recorded: list = []
    monkeypatch.setattr(incidents, "record", lambda ev, **d: recorded.append((ev, d)))

    def boom(pid, key):
        raise OSError("disk full")

    monkeypatch.setattr(connect_mod, "_save_shared_key", boom)

    connect_mod._restore_shared_key("openai", "sk-previous", "profile")  # must not raise

    assert recorded, "a failed rollback left no incident"
    event, data = recorded[0]
    assert event == incidents.SHARED_KEY_ROLLBACK_FAILED
    assert data["provider"] == "openai"
    assert "disk full" in data["error"]
    assert data["had_previous_key"] is True


def test_the_incident_has_an_operator_facing_description():
    """`navig doctor` renders DESCRIPTIONS by name — an id without one prints raw."""
    from navig.core import incidents

    assert incidents.SHARED_KEY_ROLLBACK_FAILED in incidents.DESCRIPTIONS
    assert "connect add" in incidents.DESCRIPTIONS[incidents.SHARED_KEY_ROLLBACK_FAILED]


def test_recording_failure_never_masks_the_rollback_outcome(monkeypatch):
    """Telemetry must not turn a logged failure into a raised one."""
    from navig.core import incidents

    monkeypatch.setattr(incidents, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(connect_mod, "_save_shared_key", lambda *a: (_ for _ in ()).throw(OSError("y")))

    connect_mod._restore_shared_key("openai", "sk-previous", "profile")  # must not raise


# ── the TRANSIENT branch, reachable for the first time ───────────────────
#
# FakeDriver could only fail as INVALID, so connect_provider's "transient does
# NOT prove the key is bad — stay CONNECTED" branch was covered by no test.


async def test_a_degraded_provider_keeps_the_new_key_and_stays_routable(store, shared_keys):
    """A rate limit / outage during `connect add` is not a bad key: keep the
    key the user typed, stay CONNECTED, surface the health downgrade."""
    from navig.providers.connection_types import HealthState

    drv = FakeDriver(healthy=False, failure_health=HealthState.DEGRADED.value)

    conn = await connect_provider("openai-api", api_key="sk-new", store=store, driver=drv)

    assert conn.auth_state == AuthState.CONNECTED, "a transient demoted the connection"
    assert Capability.INFERENCE in conn.capabilities, "a transient dropped inference"
    assert shared_keys["openai"] == "sk-new", "a transient rolled the key back"
    assert conn.health_state != HealthState.HEALTHY, "the downgrade must still be visible"
