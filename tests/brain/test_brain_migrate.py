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
            "config_dir": "/home/tester/.navig",
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

def test_ssh_target_inventory_reads_the_miniapp_facts_without_the_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Mini App travels with the move: same public URL, same bundle version.

    The three facts come over ONE round trip and never `config get deck` as a whole —
    that section carries deck.api_key, and this output lands in the migration log.
    """
    remote = mig.SshRemote("outpost")
    seen: list[str] = []

    def fake_remote(*cmd: str) -> tuple[int, str, str]:
        seen.append(" ".join(cmd))
        joined = " ".join(cmd)
        if "navig-shell-probe" in joined:
            return 0, "navig-shell-probe", ""
        if "cron_jobs.json" in joined:
            return 0, json.dumps({"jobs": []}), ""
        if "ls -1" in joined:
            return 0, "default\n", ""
        return 0, "https://navig-deck.a.workers.dev\nTrue\n3.31.0\n", ""

    monkeypatch.setattr(remote, "_remote", fake_remote)
    monkeypatch.setattr(remote, "_json", lambda *a, **k: {"plugins": []})
    inv = remote.target_inventory()
    assert inv["client"] == {
        "public_url": "https://navig-deck.a.workers.dev",
        "telegram_only": True,
        "deployed_version": "3.31.0",
    }
    config_lines = [c for c in seen if "config get" in c]
    assert config_lines, "the client facts must be read from the target"
    assert not any("config get deck " in c or c.endswith("config get deck") for c in config_lines)
    # A non-login ssh shell has no ~/.local/bin, so `navig` travels with its PATH.
    assert all('PATH="$HOME/.local/bin:$PATH"' in c for c in config_lines)


def test_ssh_target_inventory_reports_no_client_facts_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = mig.SshRemote("outpost")

    def fake_remote(*cmd: str) -> tuple[int, str, str]:
        joined = " ".join(cmd)
        if "navig-shell-probe" in joined:
            return 0, "navig-shell-probe", ""
        if "cron_jobs.json" in joined:
            return 0, "{}", ""
        if "ls -1" in joined:
            return 0, "", ""
        return 127, "", "bash: navig: command not found"

    monkeypatch.setattr(remote, "_remote", fake_remote)
    monkeypatch.setattr(remote, "_json", lambda *a, **k: {})
    inv = remote.target_inventory()
    assert inv["client"] == {}


def test_ssh_target_inventory_refuses_to_guess_when_the_host_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable target is an ERROR, never "it is missing everything".

    Live case (OUTPOST, 2026-10-25): `navig brain migrate --to neuron` on the brain
    answered "jobs missing on the target: <28 names>" for a hostname ssh could not
    resolve. Empty reads must not become a parity verdict.
    """
    remote = mig.SshRemote("neuron")

    def fake_remote(*cmd: str) -> tuple[int, str, str]:
        return 255, "", "ssh: Could not resolve hostname neuron: Name or service not known\n"

    monkeypatch.setattr(remote, "_remote", fake_remote)
    monkeypatch.setattr(remote, "_json", lambda *a, **k: {})
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        remote.target_inventory()
    message = str(excinfo.value)
    assert "could not reach neuron" in message
    assert "Could not resolve hostname" in message


def test_ssh_target_inventory_still_reports_a_reachable_machine_with_nothing_on_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The honest empty case must keep working: exit 1 from a missing file is not 255."""
    remote = mig.SshRemote("outpost")

    def fake_remote(*cmd: str) -> tuple[int, str, str]:
        joined = " ".join(cmd)
        if "navig-shell-probe" in joined:
            return 0, "navig-shell-probe", ""
        if "ls -1" in joined:
            return 0, "", ""
        return 1, "", "cat: /home/tester/.navig/scheduler/cron_jobs.json: No such file or directory\n"

    monkeypatch.setattr(remote, "_remote", fake_remote)
    monkeypatch.setattr(remote, "_json", lambda *a, **k: {})
    inv = remote.target_inventory()
    assert inv["jobs"] == []
    assert inv["spaces"] == []
    assert inv["client"] == {}


def test_a_service_that_authenticates_but_gives_no_shell_is_not_an_empty_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live case (OUTPOST, 2026-10-26): the plan said 28 jobs were missing on GitHub.

    The link was fine and the key was accepted — GitHub just refuses a shell, with exit 1
    and no output, which every probe read as "this machine has nothing".
    """
    remote = mig.SshRemote("github.com")

    def fake_remote(*cmd: str) -> tuple[int, str, str]:
        return (
            1,
            "",
            "Hi navig-user! You've successfully authenticated, but GitHub does not provide shell access.",
        )

    monkeypatch.setattr(remote, "_remote", fake_remote)
    monkeypatch.setattr(remote, "_json", lambda *a, **k: {})
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        remote.target_inventory()
    message = str(excinfo.value)
    assert "shell did not answer" in message
    assert "nothing was read" in message
    assert "jobs missing" not in message


def test_a_shell_that_answers_the_wrong_thing_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 0 alone is not proof: the marker has to come back."""
    remote = mig.SshRemote("outpost")

    def fake_remote(*cmd: str) -> tuple[int, str, str]:
        return 0, "Last login: Sun Oct 25 23:00:00 2026 from 10.0.0.1", ""

    monkeypatch.setattr(remote, "_remote", fake_remote)
    monkeypatch.setattr(remote, "_json", lambda *a, **k: {})
    with pytest.raises(mig.MigrationBlocked) as excinfo:
        remote.target_inventory()
    assert "shell did not answer" in str(excinfo.value)


def test_ssh_target_inventory_re_raises_when_the_connection_dies_mid_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = mig.SshRemote("outpost")

    def fake_remote(*cmd: str) -> tuple[int, str, str]:
        if "navig-shell-probe" in " ".join(cmd):
            return 0, "navig-shell-probe", ""
        return 0, "", ""

    def fake_json(*a: object, **k: object) -> dict[str, object]:
        raise mig.TransportBlocked("could not reach outpost over ssh (lost connection)")

    monkeypatch.setattr(remote, "_remote", fake_remote)
    monkeypatch.setattr(remote, "_json", fake_json)
    with pytest.raises(mig.TransportBlocked):
        remote.target_inventory()


# ── transport vs "the host answered and had nothing" ─────────────────────────
#
# The distinction is the whole point: an empty inventory is a FACT about a host that
# answered, and a transport failure is the ABSENCE of a fact. These pin the classifier
# on the exact strings this machine's ssh prints, so a wrapper that loses exit 255 (a
# killed ssh, a dead mux socket, the Windows build exiting 1) still cannot be read as
# "the target has nothing".


@pytest.mark.parametrize(
    ("code", "err"),
    [
        (255, "ssh: Could not resolve hostname neuron: Temporary failure in name resolution"),
        (255, ""),
        (1, "ssh: connect to host outpost port 22: Connection refused"),
        (1, "Connection reset by peer"),
        (1, "ssh: connect to host 192.0.2.10 port 22: No route to host"),
        (1, "Host key verification failed."),
        (1, "Permission denied (publickey,password)."),
        (1, "kex_exchange_identification: Connection closed by remote host"),
        (1, "channel 0: open failed: connect failed: Connection timed out"),
        (1, "mux_client_request_session: read from master failed: Broken pipe"),
    ],
)
def test_transport_failure_recognises_a_dead_link(code: int, err: str) -> None:
    reason = mig.transport_failure(code, err)
    assert reason, f"a dead link went unclassified: exit {code} {err!r}"
    assert reason != f"ssh exited {code} without a reason" or not err


@pytest.mark.parametrize(
    ("code", "err"),
    [
        (0, ""),
        (1, "cat: /home/tester/.navig/scheduler/cron_jobs.json: No such file or directory"),
        (127, "bash: line 1: navig: command not found"),
        (1, "navig: error: no such option: --json"),
        (2, "ls: cannot access '/home/tester/.navig/spaces': No such file or directory"),
    ],
)
def test_transport_failure_leaves_a_real_answer_alone(code: int, err: str) -> None:
    assert mig.transport_failure(code, err) is None


def test_a_message_only_transport_failure_still_blocks_the_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit code 1 with ssh's own words must not become an empty machine."""
    remote = mig.SshRemote("outpost")

    def fake_remote(*cmd: str) -> tuple[int, str, str]:
        return 1, "", "ssh: connect to host outpost port 22: Connection refused\n"

    monkeypatch.setattr(remote, "_remote", fake_remote)
    monkeypatch.setattr(remote, "_json", lambda *a, **k: {})
    with pytest.raises(mig.TransportBlocked) as excinfo:
        remote.target_inventory()
    assert "Connection refused" in str(excinfo.value)


def test_a_spawn_timeout_is_a_transport_failure_not_an_empty_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    remote = mig.SshRemote("outpost", timeout=5.0)

    def fake_run(*a: object, **k: object) -> object:
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=5.0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(mig.TransportBlocked) as excinfo:
        remote.target_inventory()
    assert "did not answer within" in str(excinfo.value)


def test_a_missing_ssh_binary_is_a_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    remote = mig.SshRemote("outpost")

    def fake_run(*a: object, **k: object) -> object:
        raise FileNotFoundError(2, "No such file or directory", "ssh")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(mig.TransportBlocked) as excinfo:
        remote.target_inventory()
    assert "could not run ssh" in str(excinfo.value)


def test_a_bundle_hash_on_a_dead_link_is_not_reported_as_a_missing_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    from pathlib import Path

    remote = mig.SshRemote("outpost")
    monkeypatch.setattr(remote, "_remote", lambda *cmd: (255, "", "Connection refused"))
    with pytest.raises(mig.TransportBlocked) as excinfo:
        remote.transfer(Path(str(tmp_path)) / "brain.tar.gz.enc")
    assert "could not reach outpost" in str(excinfo.value)
    assert "is not on outpost" not in str(excinfo.value)
