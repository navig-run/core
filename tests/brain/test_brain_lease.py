"""Brain ownership lease — the invariants that stop a split brain.

No network, no daemon: every case drives ``LeaseStore`` against a temp config dir
with an injectable clock, so an expiry is a number and not a sleep.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from navig.brain.identity import installation_fingerprint, owner_id
from navig.brain.lease import (
    BrainOwnershipError,
    LeaseStore,
    OwnershipConflict,
    StaleEpoch,
    brain_settings,
    normalize_role,
)

MACHINE_A = "brn-aaaaaaaaaaaaaaaa"
MACHINE_B = "brn-bbbbbbbbbbbbbbbb"
MACHINE_C = "brn-cccccccccccccccc"


class Clock:
    """A hand-cranked clock so TTL arithmetic is exact.

    Starts at the wall clock: a lease written with a synthetic epoch-1970 timestamp
    would read as long expired to anything that uses ``time.time()`` directly.
    """

    def __init__(self, now: float | None = None) -> None:
        self.now = time.time() if now is None else now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def store(tmp_path: Path) -> LeaseStore:
    clock = Clock()
    store = LeaseStore(tmp_path / "brain" / "lease.json", clock=clock)
    store.test_clock = clock  # type: ignore[attr-defined]
    return store


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every case runs as if this were a fresh all-in-one install."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", "all-in-one")
    monkeypatch.setenv("NAVIG_BRAIN_TTL", "300")
    monkeypatch.delenv("NAVIG_BRAIN_AUTO_PROMOTE", raising=False)
    monkeypatch.delenv("NAVIG_BRAIN_LEASE_PATH", raising=False)


# ── the free/held lifecycle ──────────────────────────────────────────────────


def test_absent_lease_is_free_and_not_an_error(store: LeaseStore) -> None:
    assert store.read() is None
    status = store.status(oid=MACHINE_A)
    assert status["state"] == "free"
    assert status["owner"] is None
    assert status["epoch"] == 0
    assert status["can_claim"] is True


def test_acquire_records_owner_role_epoch_and_ttl(store: LeaseStore) -> None:
    lease = store.acquire(oid=MACHINE_A, role="brain", reason="always-on host")
    assert lease.owner_id == MACHINE_A
    assert lease.role == "brain"
    assert lease.epoch == 1
    assert lease.ttl == 300
    assert lease.reason == "always-on host"
    assert lease.is_live()
    assert lease.held_by(MACHINE_A)


def test_acquire_twice_from_the_same_owner_is_idempotent(store: LeaseStore) -> None:
    first = store.acquire(oid=MACHINE_A)
    clock: Clock = store.test_clock  # type: ignore[attr-defined]
    clock.advance(20)
    second = store.acquire(oid=MACHINE_A)
    # A restart of one service must NOT bump the epoch: in-flight writers of this
    # process would otherwise be fenced out by their own restart.
    assert second.epoch == first.epoch == 1
    assert second.heartbeat_at > first.heartbeat_at


def test_a_second_live_brain_is_refused(store: LeaseStore) -> None:
    store.acquire(oid=MACHINE_A, role="brain")
    with pytest.raises(OwnershipConflict) as excinfo:
        store.acquire(oid=MACHINE_B, role="brain")
    message = str(excinfo.value)
    assert MACHINE_A in message
    assert "takeover" in message  # the remedy is named, not just the refusal
    # The refusal must not have touched the lease.
    assert store.read().owner_id == MACHINE_A  # type: ignore[union-attr]


def test_expired_lease_does_not_auto_promote_by_default(store: LeaseStore) -> None:
    store.acquire(oid=MACHINE_A, role="brain", ttl=300)
    clock: Clock = store.test_clock  # type: ignore[attr-defined]
    clock.advance(301)
    assert store.read().is_expired(clock.now)  # type: ignore[union-attr]
    with pytest.raises(OwnershipConflict) as excinfo:
        store.acquire(oid=MACHINE_B, role="brain")
    assert "auto_promote" in str(excinfo.value)
    assert store.read().owner_id == MACHINE_A  # type: ignore[union-attr]


def test_auto_promotion_takes_over_an_expired_lease_when_enabled(
    store: LeaseStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.acquire(oid=MACHINE_A, role="brain", ttl=300)
    clock: Clock = store.test_clock  # type: ignore[attr-defined]
    clock.advance(400)
    monkeypatch.setenv("NAVIG_BRAIN_AUTO_PROMOTE", "true")
    lease = store.acquire(oid=MACHINE_B, role="brain")
    assert lease.owner_id == MACHINE_B
    assert lease.epoch == 2
    assert lease.takeovers[-1]["from_owner"] == MACHINE_A
    assert lease.takeovers[-1]["to_owner"] == MACHINE_B
    assert lease.takeovers[-1]["expired"] is True
    assert "auto-promoted" in lease.takeovers[-1]["reason"]


def test_takeover_is_explicit_audited_and_bumps_the_epoch(store: LeaseStore) -> None:
    store.acquire(oid=MACHINE_A, role="brain", ttl=60)
    clock: Clock = store.test_clock  # type: ignore[attr-defined]
    clock.advance(120)  # expired: the evidence a takeover is allowed to act on
    lease = store.takeover(oid=MACHINE_B, role="brain", reason="moved to the outpost")
    assert lease.owner_id == MACHINE_B
    assert lease.epoch == 2
    audit = lease.takeovers[-1]
    assert audit["from_owner"] == MACHINE_A
    assert audit["reason"] == "moved to the outpost"
    assert audit["at"] == lease.acquired_at
    assert audit["expired"] is True
    assert audit["forced"] is False


def test_takeover_requires_a_reason(store: LeaseStore) -> None:
    store.acquire(oid=MACHINE_A, role="brain")
    with pytest.raises(BrainOwnershipError) as excinfo:
        store.takeover(oid=MACHINE_B, role="brain", reason="   ")
    assert "--reason" in str(excinfo.value)


def test_takeover_of_a_live_lease_needs_force_and_the_expected_owner(
    store: LeaseStore,
) -> None:
    store.acquire(oid=MACHINE_A, role="brain")
    with pytest.raises(OwnershipConflict) as excinfo:
        store.takeover(oid=MACHINE_B, role="brain", reason="moving")
    assert "--force" in str(excinfo.value)

    with pytest.raises(OwnershipConflict) as wrong:
        store.takeover(oid=MACHINE_B, role="brain", reason="moving", force=True,
                       from_owner="brn-not-the-owner")
    # A lost race must fail instead of displacing the wrong Brain.
    assert MACHINE_A in str(wrong.value)

    lease = store.takeover(
        oid=MACHINE_B, role="brain", reason="moving", force=True, from_owner=MACHINE_A
    )
    assert lease.takeovers[-1]["forced"] is True


def test_renew_presents_the_epoch_and_a_stale_writer_is_refused(store: LeaseStore) -> None:
    first = store.acquire(oid=MACHINE_A, role="brain")
    store.takeover(
        oid=MACHINE_B, role="brain", reason="cut over", force=True, from_owner=MACHINE_A
    )
    with pytest.raises(StaleEpoch) as excinfo:
        store.renew(oid=MACHINE_A, epoch=first.epoch)
    assert "epoch 1" in str(excinfo.value)


def test_renew_from_a_non_owner_is_refused(store: LeaseStore) -> None:
    store.acquire(oid=MACHINE_A, role="brain")
    with pytest.raises(OwnershipConflict):
        store.renew(oid=MACHINE_B)


def test_release_frees_the_lease_and_keeps_the_epoch_for_fencing(store: LeaseStore) -> None:
    store.acquire(oid=MACHINE_A, role="brain")
    released = store.release(oid=MACHINE_A, epoch=1, reason="stopping")
    assert released.is_free()
    assert released.released_at is not None
    assert store.status(oid=MACHINE_A)["state"] == "released"
    # A released lease is free to claim, and the claim bumps the epoch so anything
    # still holding epoch 1 is fenced out.
    claimed = store.acquire(oid=MACHINE_B, role="brain")
    assert claimed.epoch == 2


def test_release_by_a_non_owner_is_refused(store: LeaseStore) -> None:
    store.acquire(oid=MACHINE_A, role="brain")
    with pytest.raises(OwnershipConflict):
        store.release(oid=MACHINE_B)


# ── fencing ─────────────────────────────────────────────────────────────────


def test_fenced_write_refuses_a_stale_epoch(store: LeaseStore, tmp_path: Path) -> None:
    store.acquire(oid=MACHINE_A, role="brain")
    store.takeover(
        oid=MACHINE_B, role="brain", reason="new brain", force=True, from_owner=MACHINE_A
    )
    target = tmp_path / "scheduler" / "jobs.json"
    with pytest.raises(StaleEpoch):
        store.fenced_write(target, "{}", epoch=1)
    assert not target.exists()  # nothing was written, not even a partial file

    store.fenced_write(target, '{"jobs": []}', epoch=2)
    assert json.loads(target.read_text(encoding="utf-8")) == {"jobs": []}


def test_assert_epoch_with_no_lease_refuses_any_positive_epoch(
    store: LeaseStore,
) -> None:
    with pytest.raises(StaleEpoch):
        store.assert_epoch(3)
    store.assert_epoch(None)  # None means "unfenced caller", allowed deliberately


# ── partition / restart / concurrency ───────────────────────────────────────


def test_partition_leaves_the_lease_held_and_requires_a_deliberate_step(
    store: LeaseStore,
) -> None:
    """A machine that cannot see the other's heartbeat must not promote itself."""
    store.acquire(oid=MACHINE_A, role="brain", ttl=60)
    clock: Clock = store.test_clock  # type: ignore[attr-defined]
    clock.advance(600)  # a long partition: every heartbeat was missed
    status = store.status(oid=MACHINE_B)
    assert status["state"] == "expired"
    assert status["you"]["is_owner"] is False
    assert status["can_claim"] is False
    with pytest.raises(OwnershipConflict):
        store.acquire(oid=MACHINE_B, role="brain")


def test_restart_reclaims_the_same_epoch_and_a_fresh_owner_does_not(
    store: LeaseStore,
) -> None:
    original = store.acquire(oid=MACHINE_A, role="brain")
    restarted = LeaseStore(store.path).acquire(oid=MACHINE_A, role="brain")
    assert restarted.epoch == original.epoch
    with pytest.raises(OwnershipConflict):
        LeaseStore(store.path).acquire(oid=MACHINE_B, role="brain")


def test_concurrent_promotions_produce_exactly_one_winner(
    store: LeaseStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.acquire(oid=MACHINE_A, role="brain", ttl=30)
    clock: Clock = store.test_clock  # type: ignore[attr-defined]
    clock.advance(120)
    monkeypatch.setenv("NAVIG_BRAIN_AUTO_PROMOTE", "true")

    winners: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def promote(oid: str) -> None:
        barrier.wait()
        try:
            lease = LeaseStore(store.path, clock=clock).acquire(oid=oid, role="brain")
            winners.append(lease.owner_id)
        except Exception as exc:  # noqa: BLE001 — the losers must fail loudly, not crash the test
            errors.append(exc)

    # The two racers are both NON-holders: a holder's own acquire is a renew by
    # design (a restart must not bump the epoch), so racing the old owner against a
    # challenger is a race between "revive" and "take over" and legitimately ends at
    # either epoch. Two challengers race for the SAME takeover: exactly one wins.
    threads = [
        threading.Thread(target=promote, args=(MACHINE_B,)),
        threading.Thread(target=promote, args=(MACHINE_C,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a promotion thread hung on the lease lock"

    # Whoever won, the FILE holds exactly one owner and the epoch moved exactly once.
    final = store.read()
    assert final is not None
    assert final.owner_id in (MACHINE_B, MACHINE_C)
    assert final.epoch == 2
    assert len(set(winners)) == 1, winners
    assert all(isinstance(e, OwnershipConflict) for e in errors)


# ── robustness ─────────────────────────────────────────────────────────────


def test_a_corrupt_lease_is_not_silently_treated_as_free(
    store: LeaseStore, tmp_path: Path
) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(BrainOwnershipError) as excinfo:
        store.read()
    assert "refusing to guess" in str(excinfo.value).lower()
    with pytest.raises(BrainOwnershipError):
        store.acquire(oid=MACHINE_B)


def test_a_record_missing_newer_fields_still_loads(store: LeaseStore) -> None:
    """Backward compatibility: an older lease file must not lock the install out."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"owner_id": MACHINE_A, "epoch": 4, "heartbeat_at": 999_999.0, "ttl": 300}),
        encoding="utf-8",
    )
    lease = store.read()
    assert lease is not None
    assert lease.owner_id == MACHINE_A
    assert lease.epoch == 4
    assert lease.role == "all-in-one"  # normalized from an absent field
    assert lease.released_at is None


def test_an_unexpected_epoch_or_role_is_normalized(store: LeaseStore) -> None:
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"owner_id": MACHINE_A, "epoch": "7", "heartbeat_at": 1.0, "role": "SERVER"}),
        encoding="utf-8",
    )
    lease = store.read()
    assert lease is not None
    assert lease.epoch == 7
    assert lease.role == "brain"


# ── identity + settings ─────────────────────────────────────────────────────


def test_owner_id_is_stable_and_machine_bound() -> None:
    assert owner_id() == owner_id()
    assert owner_id().startswith("brn-")
    assert len(owner_id()) == len("brn-") + 16


def test_installation_fingerprint_never_reveals_the_secret() -> None:
    fingerprint = installation_fingerprint("navig_super-secret-key")
    assert fingerprint and fingerprint not in "navig_super-secret-key"
    assert len(fingerprint) == 12
    assert installation_fingerprint("") == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "all-in-one"),
        (None, "all-in-one"),
        ("node", "client"),
        ("CLIENT", "client"),
        ("brain", "brain"),
        ("server", "brain"),
        ("executor", "executor"),
        ("all-in-one", "all-in-one"),
        ("nonsense", "all-in-one"),
    ],
)
def test_role_normalization_keeps_legacy_spellings(raw: object, expected: str) -> None:
    assert normalize_role(raw) == expected


def test_settings_default_to_all_in_one_with_manual_promotion() -> None:
    settings = brain_settings()
    assert settings["role"] == "all-in-one"
    assert settings["auto_promote"] is False
    assert settings["ttl"] == 300


def test_settings_read_the_config_file_when_env_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yaml").write_text(
        "brain:\n  role: brain\n  ttl: 600\n  auto_promote: true\n", encoding="utf-8"
    )
    monkeypatch.delenv("NAVIG_BRAIN_ROLE", raising=False)
    monkeypatch.delenv("NAVIG_BRAIN_TTL", raising=False)
    settings = brain_settings()
    assert settings["role"] == "brain"
    assert settings["ttl"] == 600
    assert settings["auto_promote"] is True


def test_onboarding_role_node_is_a_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yaml").write_text("onboarding:\n  role: node\n", encoding="utf-8")
    monkeypatch.delenv("NAVIG_BRAIN_ROLE", raising=False)
    assert brain_settings()["role"] == "client"
