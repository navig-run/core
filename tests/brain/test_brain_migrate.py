"""Guided migration: resumable stages, a receipt that cannot lie, and identity.

Every case injects a remote so nothing here touches a network or a second machine.
The behaviours that matter are the ones the last "migration complete" got wrong:
an interrupted transfer must resume where it stopped, an unreconciled target must
block, and completion must be refused unless the remote's capabilities verified AND
the source is fenced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from navig.brain import migrate as mig
from navig.brain.identity import installation_fingerprint, owner_id
from navig.brain.lease import ROLE_BRAIN, LeaseStore

#: The source install's identity. The migration must prove this MOVED, and the receipt
#: must never contain it.
DECK_KEY = "navig_test_deck_key_0123456789abcdef"
DECK_FP = installation_fingerprint(DECK_KEY)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    # A real source Brain has an identity; without one there is nothing to preserve.
    (cfg / "config.yaml").write_text(
        f"deck:\n  api_key: {DECK_KEY}\nbrain:\n  role: brain\n", encoding="utf-8"
    )
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", "brain")
    monkeypatch.delenv("NAVIG_BRAIN_AUTO_PROMOTE", raising=False)


def _capabilities(**overrides: dict) -> dict[str, dict]:
    base = {
        "telegram": {"verified": True, "applicable": True, "detail": "polling owns delivery"},
        "scheduler": {"verified": True, "applicable": True, "detail": "3 enabled jobs"},
        "mail_watcher": {
            "verified": False,
            "applicable": False,
            "detail": "the mail watcher is not enabled for this install",
        },
        "api": {"verified": True, "applicable": True, "detail": "gateway on port 8789"},
    }
    base.update(overrides)
    return base


class FakeRemote:
    """A scripted target machine. Records every call so resumption is provable."""

    def __init__(self, *, tmp_path: Path) -> None:
        self.calls: list[str] = []
        self.fail_on: set[str] = set()
        self.capabilities = _capabilities()
        self.deck_key_fingerprint = DECK_FP
        self.unreconciled: list[str] = []
        self.started_target = True
        self.identity_rotation_confirmed = False
        self._bundle: Path | None = None
        self._tmp = tmp_path

    def _note(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail_on:
            raise ConnectionError(f"injected fault in {name}")

    # ── protocol ─────────────────────────────────────────────────────────
    def discover(self) -> dict[str, Any]:
        self._note("discover")
        return {
            "host": "outpost",
            "reachable": True,
            "platform": "linux",
            "navig_version": "3.28.0",
            "config_dir": "/home/void/.navig",
            "has_config": True,
        }

    def inspect(self) -> dict[str, Any]:
        self._note("inspect")
        return {
            "role": "brain",
            "owner_id": "brn-outpost00000000",
            "existing_lease": {"state": "free", "owner_id": ""},
            "telegram_mode": "off",
            "scheduler_jobs": 0,
            "deck_key_fingerprint": "",
        }

    def backup(self, destination: Path) -> dict[str, Any]:
        self._note("backup")
        destination.mkdir(parents=True, exist_ok=True)
        bundle = destination / "brain-test.tar.gz.enc"
        bundle.write_bytes(b"x" * 1024)
        self._bundle = bundle
        import hashlib

        return {
            "path": str(bundle),
            "bytes": bundle.stat().st_size,
            "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "items": 61,
        }

    def transfer(self, bundle: Path) -> dict[str, Any]:
        self._note("transfer")
        import hashlib

        return {"transferred": True, "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest()}

    def validate(self, receipt: mig.MigrationReceipt) -> dict[str, Any]:
        self._note("validate")
        return {
            "ok": not self.unreconciled,
            "reconciled": ["hosts", "databases"],
            "unreconciled": list(self.unreconciled),
            "item_counts": {"hosts": 9, "databases": 61},
            "deck_key_fingerprint": self.deck_key_fingerprint,
            "identity_rotation_confirmed": self.identity_rotation_confirmed,
        }

    def cut_over(self, receipt: mig.MigrationReceipt) -> dict[str, Any]:
        self._note("cutover")
        return {"started_target": self.started_target, "detail": ""}

    def verify(self) -> dict[str, dict[str, Any]]:
        self._note("verify")
        return self.capabilities

    def rollback(self, receipt: mig.MigrationReceipt) -> dict[str, Any]:
        self._note("rollback")
        return {"target_stopped": True}


@pytest.fixture()
def remote(tmp_path: Path) -> FakeRemote:
    return FakeRemote(tmp_path=tmp_path)


@pytest.fixture()
def runner(tmp_path: Path, remote: FakeRemote) -> mig.MigrationRunner:
    return mig.MigrationRunner(
        remote,
        store=mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations"),
        lease_store=LeaseStore(tmp_path / "cfg" / "brain" / "lease.json"),
    )


# ── the happy path ─────────────────────────────────────────────────────────


def test_a_verified_migration_completes_and_writes_an_auditable_receipt(
    runner: mig.MigrationRunner, remote: FakeRemote, tmp_path: Path
) -> None:
    LeaseStore(tmp_path / "cfg" / "brain" / "lease.json").acquire(
        oid=owner_id(), role=ROLE_BRAIN
    )
    receipt = runner.run(target_host="outpost", reason="move to the always-on host")

    assert receipt.state == "complete", receipt.blockers
    assert [s["name"] for s in receipt.stages] == list(mig.STAGES)
    assert all(s["state"] == "done" for s in receipt.stages), receipt.stages
    assert receipt.target["host"] == "outpost"
    assert receipt.capabilities["telegram"]["verified"] is True
    assert receipt.identity["deck_key_fingerprint"] == DECK_FP
    assert receipt.rollback["source_fenced"] is True
    assert receipt.rollback["performed"] is False
    assert any("navig move undo" in step for step in receipt.rollback["steps"])

    # It is on disk, parseable, and named by id — that is what "auditable" means.
    saved = json.loads(runner.store.path_for(receipt.id).read_text(encoding="utf-8"))
    assert saved["state"] == "complete"
    assert saved["id"] == receipt.id


def test_cutover_fences_the_source_with_the_existing_marker(
    runner: mig.MigrationRunner, tmp_path: Path
) -> None:
    receipt = runner.run(target_host="outpost")
    from navig.move.marker import read_moved

    marker = read_moved()
    assert marker is not None, "the source must be fenced by the migration"
    assert marker["to"] == "outpost"
    assert marker["migration"] == receipt.id
    assert receipt.rollback["moved_marker"] == str(
        tmp_path / "cfg" / "daemon" / "moved.json"
    )


# ── completion is a verified claim ─────────────────────────────────────────


def test_completion_is_refused_when_a_required_capability_is_unverified(
    runner: mig.MigrationRunner, remote: FakeRemote
) -> None:
    remote.capabilities = _capabilities(
        telegram={"verified": False, "applicable": True, "detail": "webhook owned by another owner"}
    )
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        runner.run(target_host="outpost")
    assert "REFUSING" in str(excinfo.value)
    assert "telegram" in str(excinfo.value)

    receipt = runner.store.latest()
    assert receipt is not None
    assert receipt.state == "blocked"
    assert receipt.stage_state("complete") == "failed"
    assert any("telegram" in blocker for blocker in receipt.blockers)


def test_a_capability_the_install_does_not_use_does_not_block_but_is_visible(
    runner: mig.MigrationRunner, remote: FakeRemote
) -> None:
    # mail_watcher is applicable=False in the default fixture: satisfied, not verified.
    receipt = runner.run(target_host="outpost")
    assert receipt.state == "complete"
    assert receipt.capabilities["mail_watcher"]["verified"] is False
    assert receipt.capabilities["mail_watcher"]["applicable"] is False


def test_an_installed_package_is_not_success(runner: mig.MigrationRunner) -> None:
    """`discover` reporting a version and a config dir has never been the bar."""
    receipt = runner.plan(target_host="outpost")
    assert receipt.capability("telegram") is None
    assert "telegram" in receipt.unverified_capabilities()
    assert "api" in receipt.unverified_capabilities()


def test_completion_is_refused_when_the_source_is_not_fenced(
    runner: mig.MigrationRunner,
) -> None:
    receipt = runner.plan(target_host="outpost")
    for stage in mig.STAGES:
        receipt.mark(stage, "done")
    receipt.capabilities = {name: {"name": name, "verified": True} for name in mig.REQUIRED_CAPABILITIES}
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        runner._stage_complete(receipt)
    assert "source not fenced" in str(excinfo.value)
    assert receipt.state == "blocked"


# ── interruption, resume, faults ───────────────────────────────────────────


def test_an_interrupted_transfer_resumes_without_redoing_the_backup(
    remote: FakeRemote, tmp_path: Path
) -> None:
    store = mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations")
    lease = LeaseStore(tmp_path / "cfg" / "brain" / "lease.json")
    runner = mig.MigrationRunner(remote, store=store, lease_store=lease)

    remote.fail_on = {"transfer"}
    with pytest.raises(ConnectionError):
        runner.run(target_host="outpost")

    interrupted = store.latest()
    assert interrupted is not None
    assert interrupted.state == "failed"
    assert interrupted.stage_state("backup") == "done"
    assert interrupted.stage_state("transfer") == "failed"
    assert interrupted.resume_from() == "transfer"
    transfer_failure = interrupted.stage("transfer") or {}
    assert "ConnectionError" in str(transfer_failure.get("error") or "")

    # Resume: the back-up is a fact already; it must not run again.
    remote.fail_on = set()
    remote.calls.clear()
    resumed = runner.run(target_host="outpost", resume=interrupted)
    assert resumed.state == "complete", resumed.blockers
    assert "backup" not in remote.calls, remote.calls
    assert remote.calls[0] == "transfer", remote.calls


def test_a_corrupted_transfer_is_caught_by_the_digest(
    runner: mig.MigrationRunner, remote: FakeRemote
) -> None:
    # A truncated upload: the digest the target reports does not match the back-up.
    original = remote.transfer

    def truncated(bundle: Path) -> dict:
        original(bundle)
        return {"transferred": True, "sha256": "0" * 64}

    remote.transfer = truncated  # type: ignore[method-assign]
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        runner.run(target_host="outpost")
    assert "does not match the back-up" in str(excinfo.value)
    receipt = runner.store.latest()
    assert receipt is not None
    assert receipt.stage_state("transfer") == "failed"
    assert receipt.stage_state("cutover") != "done"  # nothing was cut over


def test_a_target_that_does_not_start_blocks_at_cutover(
    runner: mig.MigrationRunner, remote: FakeRemote
) -> None:
    remote.started_target = False
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        runner.run(target_host="outpost")
    assert "did not start" in str(excinfo.value)


def test_dry_run_changes_nothing(remote: FakeRemote, tmp_path: Path) -> None:
    runner = mig.MigrationRunner(
        remote,
        store=mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations"),
        lease_store=LeaseStore(tmp_path / "cfg" / "brain" / "lease.json"),
        dry_run=True,
    )
    receipt = runner.run(target_host="outpost")
    assert receipt.state == "dry-run"
    assert remote.calls == [], remote.calls
    from navig.move.marker import read_moved

    assert read_moved() is None


# ── dedupe / reconciliation ────────────────────────────────────────────────


def test_an_unreconciled_target_blocks_before_cutover(
    runner: mig.MigrationRunner, remote: FakeRemote
) -> None:
    remote.unreconciled = ["databases: 61 on the source, 58 on the target"]
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        runner.run(target_host="outpost")
    assert "does not match the source" in str(excinfo.value)
    receipt = runner.store.latest()
    assert receipt is not None
    assert receipt.stage_state("cutover") != "done"
    evidence = (receipt.stage("validate") or {}).get("evidence") or {}
    assert evidence["unreconciled"] == remote.unreconciled
    assert evidence["reconciled"] == ["hosts", "databases"]


def test_reconciliation_evidence_survives_into_the_receipt(
    runner: mig.MigrationRunner, remote: FakeRemote
) -> None:
    receipt = runner.run(target_host="outpost")
    evidence = (receipt.stage("validate") or {}).get("evidence") or {}
    assert evidence["item_counts"] == {"hosts": 9, "databases": 61}
    assert evidence["identity_preserved"] is True


# ── identity preservation ──────────────────────────────────────────────────


def test_a_different_deck_key_fingerprint_blocks_unless_rotation_is_confirmed(
    runner: mig.MigrationRunner, remote: FakeRemote
) -> None:
    remote.deck_key_fingerprint = installation_fingerprint("a-different-key")
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        runner.run(target_host="outpost")
    assert "re-identify" in str(excinfo.value)
    assert "--rotate-identity" in str(excinfo.value)

    # The same migration is allowed when the operator says the change is deliberate.
    remote.identity_rotation_confirmed = True
    runner2 = mig.MigrationRunner(
        remote,
        store=mig.MigrationStore(runner.store.directory),
        lease_store=runner.lease_store,
    )
    receipt = runner2.run(target_host="outpost")
    assert receipt.state == "complete"
    assert receipt.identity.get("rotated") is True


def test_the_receipt_never_contains_the_deck_key(
    runner: mig.MigrationRunner, remote: FakeRemote
) -> None:
    receipt = runner.run(target_host="outpost")
    # The fingerprint is recorded; the credential is not anywhere in the receipt.
    assert receipt.identity["deck_key_fingerprint"] == DECK_FP
    assert "navig_" not in json.dumps(receipt.identity)
    assert "deck_api_key" not in json.dumps(receipt.to_dict())


# ── rollback ───────────────────────────────────────────────────────────────


def test_rollback_clears_the_fence_and_reclaims_the_source(
    runner: mig.MigrationRunner, remote: FakeRemote, tmp_path: Path
) -> None:
    receipt = runner.run(target_host="outpost")
    from navig.move.marker import read_moved

    assert read_moved() is not None

    rolled = runner.rollback(receipt)
    assert rolled.state == "rolled_back"
    assert rolled.rollback["performed"] is True
    assert rolled.rollback["marker_cleared"] is True
    assert rolled.rollback["lease_reclaimed"] is True
    assert rolled.rollback["target_stopped"] is True
    assert read_moved() is None
    # The source is a Brain again, and it says so in the lease.
    lease = LeaseStore(tmp_path / "cfg" / "brain" / "lease.json").read()
    assert lease is not None
    assert lease.owner_id == owner_id()


# ── backward compatibility ─────────────────────────────────────────────────


def test_a_minimal_receipt_from_an_older_version_still_loads(tmp_path: Path) -> None:
    store = mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations")
    store.directory.mkdir(parents=True, exist_ok=True)
    (store.directory / "old.json").write_text(
        json.dumps({"id": "old", "state": "complete", "stages": [{"name": "discover"}]}),
        encoding="utf-8",
    )
    receipt = store.load("old")
    assert receipt is not None
    assert receipt.id == "old"
    assert receipt.state == "complete"
    assert receipt.target == {}  # absent fields default instead of exploding
    # A stage entry with no state and no finish time is NOT counted as done — counting
    # it done would resume a migration past the stage that actually failed.
    assert receipt.stage_state("discover") == "pending"
    assert receipt.resume_from() == "discover"
    assert receipt.unverified_capabilities()  # no capabilities recorded yet -> gate holds


def test_receipts_are_listed_in_creation_order(runner: mig.MigrationRunner) -> None:
    first = runner.run(target_host="outpost")
    second = runner.run(target_host="outpost")
    listed = runner.store.list()
    assert [r.id for r in listed] == [first.id, second.id]
    assert runner.store.latest() is not None
    assert runner.store.latest().id == second.id  # type: ignore[union-attr]


# ── an operator-supplied bundle ────────────────────────────────────────────


def test_a_supplied_bundle_is_recorded_with_a_real_digest(
    remote: FakeRemote, tmp_path: Path
) -> None:
    """The supported live path: the operator exports, the receipt records what moved."""
    import hashlib

    bundle = tmp_path / "brain-20261022.tar.gz.enc"
    bundle.write_bytes(b"payload" * 100)
    runner = mig.MigrationRunner(
        remote,
        store=mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations"),
        lease_store=LeaseStore(tmp_path / "cfg" / "brain" / "lease.json"),
        bundle=bundle,
    )
    receipt = runner.run(target_host="outpost")
    evidence = (receipt.stage("backup") or {}).get("evidence") or {}
    assert evidence["supplied"] is True
    assert evidence["path"] == str(bundle)
    assert evidence["sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert "backup" not in remote.calls


def test_a_missing_bundle_blocks_instead_of_inventing_one(
    remote: FakeRemote, tmp_path: Path
) -> None:
    runner = mig.MigrationRunner(
        remote,
        store=mig.MigrationStore(tmp_path / "cfg" / "brain" / "migrations"),
        lease_store=LeaseStore(tmp_path / "cfg" / "brain" / "lease.json"),
        bundle=tmp_path / "nope.tar.gz.enc",
    )
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        runner.run(target_host="outpost")
    assert "does not exist" in str(excinfo.value)


# ── the real SSH transport's shaping (no network: the plumbing is stubbed) ─


def test_ssh_inspect_flattens_the_status_document(monkeypatch: pytest.MonkeyPatch) -> None:
    remote = mig.SshRemote("outpost")
    monkeypatch.setattr(
        remote,
        "_json",
        lambda *a, **k: {
            "ok": True,
            "brain": {"state": "held", "epoch": 4, "you": {"is_owner": True}, "role": "brain"},
            "identity": {"deck_key_fingerprint": "abc123abc123"},
        },
    )
    flat = remote.inspect()
    assert flat["state"] == "held"
    assert flat["deck_key_fingerprint"] == "abc123abc123"
    assert flat["you"]["is_owner"] is True


def test_ssh_cut_over_verifies_the_target_actually_owns_the_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = mig.SshRemote("outpost")
    receipt = mig.MigrationRunner(remote).plan(target_host="outpost")
    monkeypatch.setattr(
        remote, "inspect", lambda: {"state": "held", "epoch": 2, "you": {"is_owner": True}}
    )
    assert remote.cut_over(receipt)["started_target"] is True

    monkeypatch.setattr(
        remote, "inspect", lambda: {"state": "free", "epoch": 0, "you": {"is_owner": False}}
    )
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        remote.cut_over(receipt)
    assert "does not own the Brain yet" in str(excinfo.value)


def test_ssh_transfer_refuses_when_the_bundle_never_arrived(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    remote = mig.SshRemote("outpost")
    monkeypatch.setattr(remote, "_remote", lambda *cmd: (1, "", "No such file or directory"))
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        remote.transfer(tmp_path / "brain.tar.gz.enc")
    assert "is not on outpost" in str(excinfo.value)

    monkeypatch.setattr(remote, "_remote", lambda *cmd: (0, "deadbeef  /tmp/x\n", ""))
    assert remote.transfer(tmp_path / "brain.tar.gz.enc")["sha256"] == "deadbeef"