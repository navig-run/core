"""Singleton guards: a second Brain must refuse, not take over.

The cases here mirror the incident: two machines running daemon + Telegram worker +
gateway, a Windows scheduled task that fires again after the Brain moved, a client
install that was never supposed to own anything, and a partition where the other
machine simply cannot be heard.

Nothing here touches the network; every service entry point is driven with an
isolated ``NAVIG_CONFIG_DIR`` and explicit owner ids.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from navig.brain import guard
from navig.brain.lease import (
    EXIT_OWNERSHIP_REFUSED,
    ROLE_ALL_IN_ONE,
    ROLE_BRAIN,
    ROLE_CLIENT,
    ROLE_EXECUTOR,
    SINGLETON_ROLES,
    LeaseStore,
    OwnershipConflict,
    StaleEpoch,
)

MACHINE_A = "brn-aaaaaaaaaaaaaaaa"
MACHINE_B = "brn-bbbbbbbbbbbbbbbb"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", "all-in-one")
    monkeypatch.delenv("NAVIG_BRAIN_AUTO_PROMOTE", raising=False)
    monkeypatch.delenv("NAVIG_BRAIN_LEASE_PATH", raising=False)
    guard.reset_process_lease()


@pytest.fixture()
def store(tmp_path: Path) -> LeaseStore:
    return LeaseStore(tmp_path / "cfg" / "brain" / "lease.json")


# ── the fresh local install keeps working ──────────────────────────────────


def test_all_in_one_acquires_automatically_when_the_lease_is_free(
    store: LeaseStore,
) -> None:
    lease = guard.require_ownership(service="gateway", store=store)
    assert lease.owner_id == guard.current_owner()
    assert lease.role == ROLE_ALL_IN_ONE
    assert lease.epoch == 1
    # The process token is what every fenced write presents.
    assert guard.current_epoch() == 1


def test_every_singleton_renews_the_same_lease_rather_than_fighting_it(
    store: LeaseStore,
) -> None:
    first = guard.require_ownership(service="daemon", store=store)
    for service in ("gateway", "telegram", "scheduler"):
        renewed = guard.require_ownership(service=service, store=store)
        assert renewed.epoch == first.epoch, service
        assert renewed.owner_id == first.owner_id, service


# ── the incident: a second live Brain ──────────────────────────────────────


@pytest.mark.parametrize("service", ["daemon", "gateway", "telegram", "scheduler"])
def test_every_singleton_refuses_when_another_live_machine_owns_the_brain(
    store: LeaseStore, service: str
) -> None:
    store.acquire(oid=MACHINE_A, role=ROLE_BRAIN)
    with pytest.raises(guard.OwnershipRefused) as excinfo:
        guard.require_ownership(service=service, store=store)
    decision = excinfo.value.decision
    assert decision.allowed is False
    assert decision.exit_code == EXIT_OWNERSHIP_REFUSED
    assert MACHINE_A in decision.message
    # Refusing must not have moved the lease: that is how a scheduled task "re-claims".
    assert store.read().owner_id == MACHINE_A  # type: ignore[union-attr]
    assert guard.current_epoch() is None


def test_a_refusal_names_the_remedy_so_the_operator_can_act(store: LeaseStore) -> None:
    store.acquire(oid=MACHINE_A, role=ROLE_BRAIN)
    decision = guard.check(service="daemon", store=store, acquire=True)
    assert decision.allowed is False
    assert "navig brain takeover" in decision.message
    assert MACHINE_A in decision.message


# ── Windows scheduled-task reactivation ────────────────────────────────────


def test_scheduled_task_reactivation_after_a_move_does_not_reclaim(
    store: LeaseStore, tmp_path: Path
) -> None:
    """The exact regression: the task fires again on the machine the Brain LEFT.

    Two independent fences must hold, and neither may touch the lease:
    the moved marker (written by the migration) and the live lease held elsewhere.
    """
    from navig.move.marker import clear_moved, write_moved

    store.acquire(oid=MACHINE_A, role=ROLE_BRAIN)
    before = store.read().to_dict()  # type: ignore[union-attr]
    write_moved({"to": "outpost", "migration": "test"})

    try:
        with pytest.raises(guard.OwnershipRefused) as excinfo:
            guard.require_ownership(service="daemon", store=store)
        assert "moved to another machine" in excinfo.value.decision.message.lower()
        after = store.read().to_dict()  # type: ignore[union-attr]
        # Identical lease: no heartbeat, no epoch bump, no new owner.
        assert after == before
        assert guard.current_epoch() is None
    finally:
        clear_moved()


def test_scheduled_task_reactivation_does_not_take_an_expired_lease_either(
    store: LeaseStore,
) -> None:
    """An expired lease is still not an invitation: promotion is manual by default."""
    store.acquire(oid=MACHINE_A, role=ROLE_BRAIN, ttl=30)
    recorded = store.read().to_dict()  # type: ignore[union-attr]
    # Simulate the TTL passing without sleeping: rewrite the heartbeat into the past.
    recorded["heartbeat_at"] = time.time() - 600
    store.path.write_text(json.dumps(recorded), encoding="utf-8")
    before = store.read().to_dict()  # type: ignore[union-attr]

    with pytest.raises(guard.OwnershipRefused) as excinfo:
        guard.require_ownership(service="daemon", store=store)
    assert "EXPIRED" in excinfo.value.decision.message
    assert store.read().to_dict() == before  # type: ignore[union-attr]


def test_auto_promotion_off_by_default_and_on_only_when_asked(
    store: LeaseStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.acquire(oid=MACHINE_A, role=ROLE_BRAIN, ttl=30)
    lease = store.read()
    assert lease is not None
    store.path.write_text(
        json.dumps({**lease.to_dict(), "heartbeat_at": time.time() - 600}), encoding="utf-8"
    )
    with pytest.raises(guard.OwnershipRefused):
        guard.require_ownership(service="daemon", store=store)

    monkeypatch.setenv("NAVIG_BRAIN_AUTO_PROMOTE", "true")
    promoted = guard.require_ownership(service="daemon", store=store)
    assert promoted.owner_id == guard.current_owner()
    assert promoted.epoch == lease.epoch + 1
    assert promoted.takeovers[-1]["from_owner"] == MACHINE_A
    assert "auto-promoted" in promoted.takeovers[-1]["reason"]


# ── roles that must never own a Brain ──────────────────────────────────────


@pytest.mark.parametrize("role", [ROLE_CLIENT, ROLE_EXECUTOR])
def test_a_client_or_executor_never_runs_a_singleton(
    store: LeaseStore, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", role)
    with pytest.raises(guard.OwnershipRefused) as excinfo:
        guard.require_ownership(service="gateway", store=store)
    message = excinfo.value.decision.message
    assert role in message
    assert "navig brain claim --role brain" in message
    # And it did not create a lease it has no business holding.
    assert store.read() is None
    assert role not in SINGLETON_ROLES


def test_a_client_is_allowed_to_inspect_without_starting_anything(
    store: LeaseStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", ROLE_CLIENT)
    decision = guard.check(service="gateway", store=store, acquire=False)
    assert decision.allowed is False
    assert decision.exit_code == EXIT_OWNERSHIP_REFUSED


# ── ownership loss (takeover / partition) ─────────────────────────────────


def test_a_takeover_fences_the_old_process_writes(store: LeaseStore) -> None:
    guard.require_ownership(service="gateway", store=store)
    epoch_before = guard.current_epoch()
    assert epoch_before is not None

    store.takeover(
        oid=MACHINE_B,
        role=ROLE_BRAIN,
        reason="moved to the outpost",
        force=True,
        from_owner=guard.current_owner(),
    )
    assert guard.write_allowed(who="scheduler") is False
    with pytest.raises(StaleEpoch):
        guard.assert_write_allowed(who="scheduler")
    with pytest.raises(StaleEpoch):
        guard.refresh(store=store)


def test_partition_is_not_ownership_loss_for_the_holder(store: LeaseStore) -> None:
    """While this process still holds the lease, it keeps working — expiry is later."""
    guard.require_ownership(service="daemon", store=store)
    assert guard.is_owner(store=store) is True
    assert guard.write_allowed(who="scheduler") is True


def test_release_on_stop_lets_another_machine_claim(store: LeaseStore) -> None:
    guard.require_ownership(service="gateway", store=store)
    assert guard.release_process_lease(store=store, reason="clean stop") is True
    assert guard.current_epoch() is None
    # A released lease is free; the next claim bumps the epoch so the old writer stays
    # fenced even though the machine is the same.
    claimed = store.acquire(oid=MACHINE_B, role=ROLE_BRAIN)
    assert claimed.epoch == 2


# ── check() is the non-raising view (status / doctor / deck) ───────────────


def test_check_reports_the_picture_without_claiming(store: LeaseStore) -> None:
    decision = guard.check(service="gateway", store=store, acquire=False)
    assert decision.allowed is True
    assert decision.lease is None  # inspecting never creates a lease
    assert store.read() is None

    store.acquire(oid=MACHINE_A, role=ROLE_BRAIN)
    blocked = guard.check(service="gateway", store=store, acquire=False)
    assert blocked.allowed is False
    assert blocked.lease is not None
    assert blocked.lease.owner_id == MACHINE_A


def test_partitioned_check_distinguishes_expired_from_live(store: LeaseStore) -> None:
    store.acquire(oid=MACHINE_A, role=ROLE_BRAIN, ttl=30)
    lease = store.read()
    assert lease is not None
    store.path.write_text(
        json.dumps({**lease.to_dict(), "heartbeat_at": time.time() - 600}), encoding="utf-8"
    )
    decision = guard.check(service="daemon", store=store, acquire=False)
    assert decision.allowed is False
    assert "expired" in decision.message.lower()
    assert "takeover" in decision.message


def test_a_corrupt_lease_refuses_instead_of_starting_a_second_brain(
    store: LeaseStore,
) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ truncated", encoding="utf-8")
    with pytest.raises(guard.OwnershipRefused) as excinfo:
        guard.require_ownership(service="daemon", store=store)
    assert "refusing to guess" in str(excinfo.value).lower()
    assert store.path.read_text(encoding="utf-8") == "{ truncated"  # untouched


# ── duplicate-brain detection helpers ─────────────────────────────────────


def test_owner_id_differs_between_machines_by_construction() -> None:
    a = LeaseStore().acquire(oid=MACHINE_A, role=ROLE_BRAIN)
    assert a.owner_id != MACHINE_B
    with pytest.raises(OwnershipConflict):
        LeaseStore().acquire(oid=MACHINE_B, role=ROLE_BRAIN)


# ── the real entry point (the Windows scheduled task's action) ─────────────


def _stub_daemon(monkeypatch) -> None:
    """If the guard leaks, constructing a supervisor must fail rather than block."""
    import navig.daemon.supervisor as supervisor

    class _NeverStarts:
        @staticmethod
        def is_running() -> bool:
            return False

        @staticmethod
        def read_pid() -> int:
            return 0

        def __init__(self, *a, **k) -> None:
            raise AssertionError("the daemon was constructed on a machine that is not the Brain")

    monkeypatch.setattr(supervisor, "NavigDaemon", _NeverStarts)


def test_daemon_entry_refuses_when_another_machine_owns_the_brain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The scheduled task's own action is guarded, not just the helper it calls."""
    import os

    import navig.daemon.entry as entry

    monkeypatch.chdir(os.getcwd())
    store = LeaseStore()
    store.acquire(oid=MACHINE_A, role=ROLE_BRAIN)
    before = store.path.read_bytes()
    _stub_daemon(monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        entry.main()
    assert excinfo.value.code == EXIT_OWNERSHIP_REFUSED
    # Exit 77, and the lease is byte-identical: a reactivation never re-claims.
    assert store.path.read_bytes() == before


def test_daemon_entry_refuses_after_a_move_and_leaves_the_lease_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A moved install stays stopped and reports SUCCESS (the operator asked for it).

    The moved marker is the older, deliberate fence (`navig move out`). Its contract is a
    quiet successful exit — Task Scheduler must not record a failure for the outcome the
    operator requested — so the assertion here is "returns, and never touches the lease".
    The LEASE guard is the one that exits 77, and it is checked after this one.
    """
    import os

    import navig.daemon.entry as entry
    from navig.move.marker import clear_moved, write_moved

    monkeypatch.chdir(os.getcwd())
    store = LeaseStore()
    store.acquire(oid=MACHINE_A, role=ROLE_BRAIN)
    before = store.path.read_bytes()
    write_moved({"to": "outpost", "migration": "test"})
    _stub_daemon(monkeypatch)
    try:
        assert entry.main() is None
        assert store.path.read_bytes() == before
    finally:
        clear_moved()
