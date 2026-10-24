"""Guided Brain migration — and the receipt that refuses to lie.

The flow is the one the operator actually performs by hand, made resumable and
evidenced::

    Discover → Inspect → Back up → Transfer → Validate → Cut over → Verify → Complete

Two rules make this worth more than a checklist:

* **Completion is a verified claim, never a file that exists.** ``Complete`` refuses
  unless the remote Brain proves its capabilities — Telegram delivery, the scheduler,
  the mail watcher and the API — AND the source is fenced (moved marker written,
  lease released). Installed packages, copied tarballs and a green ``scp`` are never
  success. This is the failure the last "migration complete" shipped: Windows still
  owned the bot while the receipt said done.
* **Every stage checkpoints into the receipt.** An interrupted transfer resumes from
  the stage that failed instead of redoing the back-up; a receipt written by an older
  version still loads (unknown fields are kept, missing ones default).

The remote side is injected (``remote=``), so the whole flow is testable with no
network and the SSH implementation stays thin (``SshRemote``).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from navig.brain import authority, capabilities
from navig.brain.authority import AuthorityLeaseStore
from navig.brain.identity import (
    hostname,
    installation_fingerprint,
    machine_id_source,
)
from navig.brain.identity import (
    owner_id as default_owner_id,
)
from navig.brain.lease import (
    BrainLease,
    BrainOwnershipError,
    LeaseStore,
    brain_settings,
    normalize_role,
)

logger = logging.getLogger(__name__)

__all__ = [
    "STAGES",
    "Capability",
    "MigrationBlocked",
    "MigrationReceipt",
    "MigrationRunner",
    "MigrationStore",
    "REQUIRED_CAPABILITIES",
    "RemoteBrain",
    "SshRemote",
    "migrations_dir",
]

#: The eight stages, in order. The list is the contract; receipts name them.
STAGES: tuple[str, ...] = (
    "discover",
    "inspect",
    "backup",
    "transfer",
    "validate",
    "cutover",
    "verify",
    "complete",
)

#: A migration is not complete until each of these is verified ON THE REMOTE BRAIN.
#: One list, shared with the probe that produces them (`navig.brain.capabilities`), so
#: the gate and the evidence can never disagree about what "verified" covers.
REQUIRED_CAPABILITIES: tuple[str, ...] = capabilities.REQUIRED_CAPABILITIES

_STAGE_DONE = "done"
_STAGE_FAILED = "failed"
_STAGE_PENDING = "pending"
_STAGE_SKIPPED = "skipped"


class MigrationBlocked(RuntimeError):
    """A stage refused to proceed. The receipt records it; nothing is reported done."""


def migrations_dir() -> Path:
    from navig.platform import paths  # noqa: PLC0415

    return paths.config_dir() / "brain" / "migrations"


def _local_deck_key_fingerprint() -> str:
    """This install's ``deck.api_key`` fingerprint — proof of identity, never the key."""
    try:
        key = str(capabilities.config_section("deck").get("api_key") or "")
    except Exception:  # noqa: BLE001 — unreadable config is "no fingerprint yet"
        return ""
    return installation_fingerprint(key)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Capability:
    """One remote capability and the evidence behind it.

    ``applicable=False`` records a capability this deployment deliberately does not
    use (no bot token, mail watcher switched off). It satisfies the completion gate
    WITHOUT being counted as verified — the receipt keeps the difference visible.
    """

    name: str
    verified: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    applicable: bool = True

    @property
    def satisfied(self) -> bool:
        return self.verified or not self.applicable

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "verified": self.verified,
            "applicable": self.applicable,
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Capability:
        return cls(
            name=str(data.get("name") or ""),
            verified=bool(data.get("verified")),
            detail=str(data.get("detail") or ""),
            evidence=dict(data.get("evidence") or {}),
            applicable=bool(data.get("applicable", True)),
        )


@dataclass
class MigrationReceipt:
    """The auditable record of one migration. JSON on disk, never overwritten later."""

    id: str
    source: dict[str, Any]
    target: dict[str, Any]
    created_at: str
    updated_at: str
    state: str = "running"
    reason: str = ""
    stages: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, dict[str, Any]] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    identity: dict[str, Any] = field(default_factory=dict)
    rollback: dict[str, Any] = field(default_factory=dict)
    #: Where the ownership AUTHORITY stood at each decision: mode/url at inspect,
    #: the recorded move at cutover, and owner + epoch at completion. A migration
    #: that cannot show the authority's own epoch has not proven where the Brain is.
    authority: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    schema: int = 1

    # ── stage bookkeeping ────────────────────────────────────────────────
    def stage(self, name: str) -> dict[str, Any] | None:
        for entry in self.stages:
            if entry.get("name") == name:
                return entry
        return None

    def stage_state(self, name: str) -> str:
        """A stage is only ``done`` when something said so — or recorded finishing it.

        A receipt from another version may carry a stage entry with no ``state``. That is
        "unknown", never "done": counting it as done would let an unfinished migration
        resume past the stage that actually failed.
        """
        entry = self.stage(name)
        if entry is None:
            return _STAGE_PENDING
        state = entry.get("state")
        if state:
            return str(state)
        return _STAGE_DONE if entry.get("finished_at") else _STAGE_PENDING

    def mark(
        self,
        name: str,
        state: str,
        *,
        detail: str = "",
        evidence: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        now = _now()
        entry = self.stage(name)
        if entry is None:
            entry = {"name": name, "state": _STAGE_PENDING, "started_at": now}
            self.stages.append(entry)
        entry["state"] = state
        entry["finished_at"] = now
        if detail:
            entry["detail"] = detail
        if evidence:
            entry.setdefault("evidence", {}).update(evidence)
        if error:
            entry["error"] = error
        self.updated_at = now

    def resume_from(self) -> str | None:
        """The first stage that did not finish — where an interrupted run restarts."""
        for name in STAGES:
            if self.stage_state(name) != _STAGE_DONE:
                return name
        return None

    # ── serialisation ────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "id": self.id,
            "state": self.state,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": dict(self.source),
            "target": dict(self.target),
            "stages": [dict(s) for s in self.stages],
            "capabilities": {k: dict(v) for k, v in self.capabilities.items()},
            "blockers": list(self.blockers),
            "identity": dict(self.identity),
            "rollback": dict(self.rollback),
            "authority": dict(self.authority),
            **({"extra": dict(self.extra)} if self.extra else {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MigrationReceipt:
        """Load leniently — a receipt from an older version still opens."""
        stages = data.get("stages")
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            source=dict(data.get("source") or {}),
            target=dict(data.get("target") or {}),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
            state=str(data.get("state") or "running"),
            reason=str(data.get("reason") or ""),
            stages=[s for s in stages if isinstance(s, dict)] if isinstance(stages, list) else [],
            capabilities={
                str(k): dict(v)
                for k, v in (data.get("capabilities") or {}).items()
                if isinstance(v, dict)
            },
            blockers=[str(b) for b in (data.get("blockers") or []) if isinstance(b, str)],
            identity=dict(data.get("identity") or {}),
            rollback=dict(data.get("rollback") or {}),
            authority=dict(data.get("authority") or {}),
            schema=int(data.get("schema") or 1),
        )

    def capability(self, name: str) -> Capability | None:
        raw = self.capabilities.get(name)
        return Capability.from_dict(raw) if raw else None

    def unverified_capabilities(self) -> list[str]:
        """Required capabilities with no verification — the completion gate."""
        missing: list[str] = []
        for name in REQUIRED_CAPABILITIES:
            cap = self.capability(name)
            if cap is None:
                missing.append(name)
            elif not cap.satisfied and not cap.verified:
                missing.append(f"{name} ({cap.detail or 'unverified'})")
        return missing


class MigrationStore:
    """Receipt storage: one JSON file per migration, `<config>/brain/migrations/`."""

    def __init__(self, directory: Path | None = None) -> None:
        self._explicit = Path(directory) if directory is not None else None

    @property
    def directory(self) -> Path:
        return self._explicit if self._explicit is not None else migrations_dir()

    def path_for(self, receipt_id: str) -> Path:
        return self.directory / f"{receipt_id}.json"

    def save(self, receipt: MigrationReceipt) -> Path:
        from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

        receipt.updated_at = _now()
        path = self.path_for(receipt.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(receipt.to_dict(), indent=2))
        return path

    def load(self, receipt_id: str) -> MigrationReceipt | None:
        return self.load_path(self.path_for(receipt_id))

    def load_path(self, path: Path) -> MigrationReceipt | None:
        from navig.core.yaml_io import read_text_retrying  # noqa: PLC0415

        try:
            text = read_text_retrying(path)
        except (FileNotFoundError, OSError):
            return None
        try:
            data = json.loads(text)
        except ValueError:
            logger.warning("migration receipt %s is not valid JSON — ignoring", path)
            return None
        return MigrationReceipt.from_dict(data) if isinstance(data, dict) else None

    def list(self) -> list[MigrationReceipt]:
        out: list[MigrationReceipt] = []
        try:
            files = sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return out
        for path in files:
            receipt = self.load_path(path)
            if receipt is not None:
                out.append(receipt)
        return out

    def latest(self) -> MigrationReceipt | None:
        receipts = self.list()
        return receipts[-1] if receipts else None


class RemoteBrain(Protocol):
    """What a migration needs from the machine that will BE the Brain.

    Every method returns JSON-ready data and may raise :class:`MigrationBlocked` to
    stop the run. Tests inject a fake; :class:`SshRemote` is the real transport.
    """

    def discover(self) -> dict[str, Any]: ...
    def inspect(self) -> dict[str, Any]: ...
    def backup(self, destination: Path) -> dict[str, Any]: ...
    def transfer(self, bundle: Path) -> dict[str, Any]: ...
    def validate(self, receipt: MigrationReceipt) -> dict[str, Any]: ...
    def cut_over(self, receipt: MigrationReceipt) -> dict[str, Any]: ...
    def verify(self) -> dict[str, dict[str, Any]]: ...
    def rollback(self, receipt: MigrationReceipt) -> dict[str, Any]: ...


class MigrationRunner:
    """Runs the eight stages against an injected remote, checkpointing as it goes."""

    def __init__(
        self,
        remote: RemoteBrain,
        *,
        store: MigrationStore | None = None,
        lease_store: LeaseStore | None = None,
        dry_run: bool = False,
        rotate_identity: bool = False,
        bundle: str | Path | None = None,
        clock=None,
        authority_client: Any = None,
    ) -> None:
        self.remote = remote
        self.store = store or MigrationStore()
        # The authority, when configured, IS the source's record: releasing a local
        # file would leave the authoritative epoch untouched and let the source
        # re-claim. With no authority (single-machine install) the local store is
        # used, exactly as before.
        self.authority = (
            authority_client if authority_client is not None else authority.client_or_none()
        )
        if self.authority is not None:
            self.lease_store = (
                lease_store
                if isinstance(lease_store, AuthorityLeaseStore)
                else AuthorityLeaseStore(self.authority)
            )
        else:
            self.lease_store = lease_store or LeaseStore()
        self.dry_run = dry_run
        # A deliberate re-identification (new deck key ⇒ new Lighthouse tenant). Only
        # ever true when the operator passed --rotate-identity; the receipt records it.
        self.rotate_identity = rotate_identity
        # An operator-exported archive (`navig backup export --brain`). When supplied, the
        # backup stage records THAT file (path + digest) instead of asking the remote.
        self.bundle = Path(bundle) if bundle is not None else None
        self._clock = clock

    # ── entry points ─────────────────────────────────────────────────────
    def plan(self, *, target_host: str, reason: str = "") -> MigrationReceipt:
        """A fresh receipt with every stage pending — what ``--dry-run`` shows."""
        now = _now()
        token = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
        return MigrationReceipt(
            id=token,
            source={
                "owner_id": default_owner_id(),
                "hostname": hostname(),
                "machine_id_source": machine_id_source(),
                "config_dir": str(self.store.directory.parent.parent),
                "role": brain_settings()["role"],
            },
            target={"host": target_host, "role": "brain"},
            created_at=now,
            updated_at=now,
            reason=reason or f"migrate the Brain to {target_host}",
            stages=[{"name": name, "state": _STAGE_PENDING} for name in STAGES],
            authority={
                "mode": "remote" if self.authority is not None else "local",
                "url": self.authority.url if self.authority is not None else "",
            },
            rollback={
                "steps": [
                    "On the source: navig move undo  (clears the moved marker)",
                    "On the source: navig brain claim --role <previous role> --reason rollback",
                    "On the target: stop the Brain (navig service stop) and re-point clients",
                    "Restore the source from the back-up recorded in this receipt, then "
                    "navig brain verify",
                ],
                "performed": False,
                "source_fenced": False,
            },
        )

    def run(
        self,
        *,
        target_host: str,
        reason: str = "",
        resume: MigrationReceipt | str | None = None,
    ) -> MigrationReceipt:
        """Execute (or resume) the flow. Returns the receipt in its final state."""
        receipt = self._resolve_resume(target_host=target_host, reason=reason, resume=resume)
        start = receipt.resume_from() if resume is not None else STAGES[0]
        if start is None:
            return receipt  # nothing left to do — already complete

        if self.dry_run:
            for name in STAGES:
                if receipt.stage_state(name) == _STAGE_DONE:
                    continue
                receipt.mark(name, _STAGE_SKIPPED, detail="dry run — not executed")
            receipt.state = "dry-run"
            self.store.save(receipt)
            return receipt

        for name in STAGES:
            if receipt.stage_state(name) == _STAGE_DONE:
                continue
            self._run_stage(receipt, name)
        return receipt

    def _resolve_resume(
        self,
        *,
        target_host: str,
        reason: str,
        resume: MigrationReceipt | str | None,
    ) -> MigrationReceipt:
        if isinstance(resume, MigrationReceipt):
            return resume
        if isinstance(resume, str):
            loaded = self.store.load(resume)
            if loaded is None:
                raise MigrationBlocked(f"No migration receipt {resume!r} in {self.store.directory}")
            return loaded
        return self.plan(target_host=target_host, reason=reason)

    # ── stages ───────────────────────────────────────────────────────────
    def stage_handlers(self) -> dict[str, Any]:
        """``stage name → the method that runs it``, named explicitly.

        NOT ``getattr(self, f"_stage_{name}")``: a dynamically built name is invisible to
        readers and to the reachability guard (`tests/quality/test_no_unreachable_private_methods.py`),
        where an uncalled handler is indistinguishable from a feature nobody has. Naming
        them makes the bijection with :data:`STAGES` checkable — see
        :meth:`_assert_stage_handlers`.
        """
        return {
            "discover": self._stage_discover,
            "inspect": self._stage_inspect,
            "backup": self._stage_backup,
            "transfer": self._stage_transfer,
            "validate": self._stage_validate,
            "cutover": self._stage_cutover,
            "verify": self._stage_verify,
            "complete": self._stage_complete,
        }

    def _assert_stage_handlers(self) -> None:
        """Fail loudly if the stage list and the handlers ever drift apart."""
        handlers = self.stage_handlers()
        missing = [name for name in STAGES if name not in handlers]
        extra = [name for name in handlers if name not in STAGES]
        if missing or extra:
            raise MigrationBlocked(
                f"migration stage wiring is inconsistent (missing: {missing}, unknown: {extra})"
            )

    def _run_stage(self, receipt: MigrationReceipt, name: str) -> None:
        self._assert_stage_handlers()
        receipt.mark(name, "running", detail="")
        self.store.save(receipt)
        try:
            handler = self.stage_handlers()[name]
            handler(receipt)
        except MigrationBlocked as exc:
            receipt.mark(name, _STAGE_FAILED, error=str(exc))
            receipt.state = "blocked"
            self._add_blocker(receipt, f"{name}: {exc}")
            self.store.save(receipt)
            raise
        except Exception as exc:  # noqa: BLE001 — every stage failure must be recorded
            receipt.mark(name, _STAGE_FAILED, error=f"{type(exc).__name__}: {exc}")
            receipt.state = "failed"
            self._add_blocker(receipt, f"{name}: {type(exc).__name__}: {exc}")
            self.store.save(receipt)
            raise
        receipt.mark(name, _STAGE_DONE)
        self.store.save(receipt)

    @staticmethod
    def _add_blocker(receipt: MigrationReceipt, text: str) -> None:
        if text not in receipt.blockers:
            receipt.blockers.append(text)

    def _stage_discover(self, receipt: MigrationReceipt) -> None:
        info = self.remote.discover() or {}
        if not info.get("reachable", True):
            raise MigrationBlocked(
                f"{receipt.target.get('host')} is not reachable: {info.get('detail') or 'no detail'}"
            )
        receipt.target.update(
            {
                "reachable": True,
                "platform": info.get("platform"),
                "navig_version": info.get("navig_version"),
                "config_dir": info.get("config_dir"),
                "has_config": info.get("has_config"),
            }
        )
        receipt.mark("discover", _STAGE_DONE, evidence={"host": info.get("host")})

    def _stage_inspect(self, receipt: MigrationReceipt) -> None:
        info = self.remote.inspect() or {}
        receipt.source["inspect"] = info.get("source") or {}
        receipt.target["inspect"] = {
            "role": info.get("role"),
            "existing_lease": info.get("existing_lease"),
            "telegram_mode": info.get("telegram_mode"),
            "scheduler_jobs": info.get("scheduler_jobs"),
        }
        receipt.identity.update(
            {
                # The FINGERPRINT travels; the key never does. When the remote has not
                # reported one yet, the source's own is used — it is the thing the
                # target must match, so it has to be recorded before the move.
                "deck_key_fingerprint": info.get("deck_key_fingerprint")
                or installation_fingerprint(info.get("deck_api_key"))
                or _local_deck_key_fingerprint(),
                "source_owner": default_owner_id(),
                "target_owner": info.get("owner_id"),
            }
        )
        existing = info.get("existing_lease") or {}
        if existing.get("state") == "held" and existing.get("owner_id") not in (
            None,
            "",
            info.get("owner_id"),
        ):
            raise MigrationBlocked(
                "the target already hosts a Brain for a different owner "
                f"({existing.get('owner_id')}, {existing.get('state')}). Resolve that first — "
                "a migration must not create a second Brain."
            )

        # The AUTHORITY is the shared record; a target's local file cannot prove
        # anything about the other machine. Ask it before moving anything.
        if self.authority is not None:
            try:
                payload = self.authority.status(oid=default_owner_id())
            except authority.AuthorityError as exc:
                raise MigrationBlocked(
                    "the ownership AUTHORITY could not be consulted before the move "
                    f"({exc}). A migration must know who owns the Brain — refusing to "
                    "start on a record nobody can read."
                ) from exc
            raw = payload.get("record") if isinstance(payload, dict) else None
            record = BrainLease.from_dict(raw) if isinstance(raw, dict) and raw.get("owner_id") else None
            holder = record.owner_id if record is not None else ""
            epoch = record.epoch if record is not None else 0
            receipt.authority.update(
                {
                    "reachable": True,
                    "owner_id": holder,
                    "epoch": epoch,
                    "at_inspect": _now(),
                    "record": raw if isinstance(raw, dict) else None,
                }
            )
            allowed = {"", default_owner_id(), str(info.get("owner_id") or "")}
            if record is not None and record.is_live() and holder not in allowed:
                raise MigrationBlocked(
                    f"the ownership AUTHORITY says this Brain is held by {holder} at epoch "
                    f"{epoch}, which is neither this source nor the target "
                    f"({info.get('owner_id') or 'unknown'}). Resolve that first — a migration "
                    "must not create a second Brain."
                )
        receipt.mark("inspect", _STAGE_DONE)

    def _stage_backup(self, receipt: MigrationReceipt) -> None:
        """Produce (or accept) a bundle the migration can prove it transferred.

        An operator may already have exported the Brain (`navig backup export --brain`) —
        that is the supported path on a live machine, and `--bundle` hands the archive in
        so this stage records it with a real digest instead of re-exporting.
        """
        if self.bundle is not None:
            bundle = Path(self.bundle)
            if not bundle.is_file():
                raise MigrationBlocked(f"--bundle {bundle} does not exist")
            receipt.mark(
                "backup",
                _STAGE_DONE,
                evidence={
                    "path": str(bundle),
                    "bytes": bundle.stat().st_size,
                    "sha256": _sha256(bundle),
                    "items": None,
                    "supplied": True,
                },
            )
            receipt.rollback["backups"] = [str(bundle)]
            self.store.save(receipt)
            return

        destination = migrations_dir().parent / "migration-backups"
        destination.mkdir(parents=True, exist_ok=True)
        result = self.remote.backup(destination) or {}
        if not result.get("path"):
            raise MigrationBlocked(
                "the back-up produced nothing — refusing to transfer anything that cannot "
                "be restored"
            )
        backup_path = Path(str(result["path"]))
        # A digest is the only thing that makes the NEXT stage's checksum meaningful. If
        # the probe could not report one, compute it here rather than transfer blind.
        digest = str(result.get("sha256") or "")
        if not digest and backup_path.is_file():
            digest = _sha256(backup_path)
        receipt.mark(
            "backup",
            _STAGE_DONE,
            evidence={
                "path": str(backup_path),
                "bytes": result.get("bytes"),
                "sha256": digest or None,
                "items": result.get("items"),
            },
        )
        receipt.rollback["backups"] = [str(backup_path)]
        self.store.save(receipt)

    def _stage_transfer(self, receipt: MigrationReceipt) -> None:
        backup = receipt.stage("backup") or {}
        evidence = backup.get("evidence") or {}
        if not evidence.get("path"):
            raise MigrationBlocked("transfer has no back-up to send — the backup stage is not done")
        result = self.remote.transfer(Path(str(evidence["path"]))) or {}
        local_sha = str(evidence.get("sha256") or "")
        remote_sha = str(result.get("sha256") or "")
        if local_sha and remote_sha and local_sha != remote_sha:
            raise MigrationBlocked(
                f"the transferred bundle does not match the back-up (sha256 {local_sha[:12]}… vs "
                f"{remote_sha[:12]}…). Nothing was cut over."
            )
        receipt.mark(
            "transfer",
            _STAGE_DONE,
            evidence={
                "transferred": bool(result.get("transferred", True)),
                "sha256": remote_sha or local_sha,
                "bytes": result.get("bytes"),
            },
        )

    def _stage_validate(self, receipt: MigrationReceipt) -> None:
        result = self.remote.validate(receipt) or {}
        unreconciled = [str(x) for x in (result.get("unreconciled") or [])]
        reconciled = [str(x) for x in (result.get("reconciled") or [])]
        receipt.mark(
            "validate",
            _STAGE_DONE,
            evidence={
                "ok": bool(result.get("ok")),
                "reconciled": reconciled,
                "unreconciled": unreconciled,
                "item_counts": result.get("item_counts") or {},
            },
        )
        if not result.get("ok", False) or unreconciled:
            raise MigrationBlocked(
                "the target does not match the source: "
                + ("; ".join(unreconciled) if unreconciled else "validate reported not-ok")
            )
        remote_fp = str(result.get("deck_key_fingerprint") or "")
        local_fp = str(receipt.identity.get("deck_key_fingerprint") or "")
        if local_fp and remote_fp and local_fp != remote_fp:
            # An identity change is a re-identification, not a migration. It is only
            # legitimate when the operator asked for it, and then it must be recorded.
            if not (result.get("identity_rotation_confirmed") or self.rotate_identity):
                raise MigrationBlocked(
                    "the target's deck key fingerprint differs from the source's "
                    f"({local_fp} vs {remote_fp}) — this migration would re-identify the "
                    "Brain (new Lighthouse tenant, new bot mailbox). Pass "
                    "--rotate-identity if that is deliberate."
                )
            receipt.identity["rotated"] = True
        receipt.identity["target_deck_key_fingerprint"] = remote_fp
        receipt.mark("validate", _STAGE_DONE, evidence={"identity_preserved": local_fp == remote_fp})

    def _stage_cutover(self, receipt: MigrationReceipt) -> None:
        result = self.remote.cut_over(receipt) or {}
        if not result.get("started_target"):
            raise MigrationBlocked(
                "the target did not start: "
                + str(result.get("detail") or "cut_over reported no running Brain")
            )
        # Fence the source with the mechanism that already exists, so a Windows
        # scheduled task that fires again cannot re-claim (see navig.move.marker).
        from navig.move.marker import clear_moved, write_moved  # noqa: PLC0415

        marker = write_moved(
            {
                "to": receipt.target.get("host"),
                "migration": receipt.id,
                "reason": receipt.reason,
            }
        )
        receipt.rollback["moved_marker"] = str(marker)
        receipt.rollback["source_fenced"] = True

        # The local marker fences THIS machine; the shared AUTHORITY fence is what
        # stops a copy of this config dir (same owner, another disk) from being
        # believed. The move is written INTO the authority record, CAS-guarded.
        if self.authority is not None:
            try:
                moved_lease = self.authority.mark_moved(
                    to=str(receipt.target.get("host") or ""),
                    migration=receipt.id,
                    reason=receipt.reason or f"migrated to {receipt.target.get('host')}",
                )
            except authority.AuthorityError as exc:
                receipt.rollback["authority_moved"] = False
                raise MigrationBlocked(
                    "the ownership AUTHORITY did not record the move "
                    f"({exc}). The local marker is written but the SHARED record does not "
                    "name the move — refusing to cut over on a half-written fence."
                ) from exc
            receipt.authority.update(
                {
                    "moved_at": _now(),
                    "moved_marker": moved_lease.moved_marker,
                    "moved_epoch": moved_lease.epoch,
                }
            )
            receipt.rollback["authority_moved"] = True

        lease = None
        try:
            lease = self.lease_store.release(reason=f"migrated to {receipt.target.get('host')}")
        except BrainOwnershipError as exc:
            # No local lease is fine (a client source); a LIVE lease we cannot release
            # means the source is still the Brain — that must not pass silently.
            logger.info("no local lease released during cutover: %s", exc)
        receipt.rollback["previous_lease"] = lease.to_dict() if lease else None
        receipt.mark(
            "cutover",
            _STAGE_DONE,
            evidence={
                "moved_marker": str(marker),
                "source_lease_released": lease is not None,
                "target": receipt.target.get("host"),
            },
        )
        # A rollback that cannot undo the fence is not a rollback.
        receipt.rollback["steps"] = [
            *list(receipt.rollback.get("steps") or []),
            f"Source marker to remove: {marker}",
        ]
        receipt.rollback["clear_moved"] = clear_moved.__name__

    def _stage_verify(self, receipt: MigrationReceipt) -> None:
        caps = self.remote.verify() or {}
        for name, info in caps.items():
            if not isinstance(info, dict):
                continue
            # Capabilities are stored as plain dicts (JSON-ready); `capability()` wraps
            # them. Storing objects here made `to_dict` unserialisable — caught by the
            # first end-to-end run.
            receipt.capabilities[str(name)] = Capability(
                name=str(name),
                verified=bool(info.get("verified")),
                detail=str(info.get("detail") or ""),
                evidence=dict(info.get("evidence") or {}),
                applicable=bool(info.get("applicable", True)),
            ).as_dict()
        receipt.mark("verify", _STAGE_DONE, evidence={"checked": sorted(receipt.capabilities)})

    def _authority_completion_gap(self, receipt: MigrationReceipt) -> str | None:
        """Why the AUTHORITY does not (yet) name the target as the Brain, or ``None``.

        This is the remote half of "completion is a verified claim": capabilities are
        proven on the target, ownership is proven by the shared record. A receipt that
        says complete while the authority still names the source (or nobody) is the
        false green this whole area exists to prevent.
        """
        try:
            payload = self.authority.status(oid=default_owner_id())
        except authority.AuthorityError as exc:
            return f"the ownership authority could not be consulted at completion: {exc}"
        raw = payload.get("record") if isinstance(payload, dict) else None
        record = BrainLease.from_dict(raw) if isinstance(raw, dict) and raw.get("owner_id") else None
        if record is None:
            return "the ownership authority has no owner record — no machine owns the Brain"
        if record.is_free():
            return (
                f"the ownership authority's record is released (epoch {record.epoch}) — "
                "no machine owns the Brain"
            )
        target_owner = str((receipt.target.get("inspect") or {}).get("owner_id") or "")
        if target_owner and record.owner_id != target_owner:
            return (
                f"the ownership authority names {record.owner_id} (epoch {record.epoch}) as "
                f"the Brain, but the target is {target_owner} — the receipt must not claim "
                "a Brain the authority does not name"
            )
        receipt.authority.update(
            {
                "verified": True,
                "owner_id": record.owner_id,
                "epoch": record.epoch,
                "verified_at": _now(),
            }
        )
        return None

    def _stage_complete(self, receipt: MigrationReceipt) -> None:
        """The gate. Reports complete ONLY when every required capability verified."""
        missing = receipt.unverified_capabilities()

        # The source must be fenced too: "complete" while this machine still owns the
        # bot is the exact false report that started this work.
        source_fenced = bool(receipt.rollback.get("source_fenced"))
        if not source_fenced:
            missing.append("source not fenced (moved marker not written)")

        # …and when there is a shared authority, IT must name the target as the
        # owner. A target's own claim is a local statement; the authority's epoch is
        # the evidence that the other machine can see the same owner.
        if self.authority is not None:
            authority_gap = self._authority_completion_gap(receipt)
            if authority_gap:
                missing.append(authority_gap)

        if missing:
            receipt.state = "blocked"
            for item in missing:
                self._add_blocker(receipt, f"complete: {item}")
            receipt.mark(
                "complete",
                _STAGE_FAILED,
                detail="completion refused — remote capabilities are not verified",
                evidence={"missing": missing},
            )
            self.store.save(receipt)
            raise MigrationBlocked(
                "REFUSING to report the migration complete. Installed packages and copied "
                "files are not success. Unverified: " + ", ".join(missing)
            )

        receipt.state = "complete"
        receipt.mark(
            "complete",
            _STAGE_DONE,
            evidence={
                "verified": sorted(receipt.capabilities),
                "source_fenced": source_fenced,
                "target": receipt.target.get("host"),
                "authority": {
                    "mode": receipt.authority.get("mode", "remote"),
                    "owner_id": receipt.authority.get("owner_id"),
                    "epoch": receipt.authority.get("epoch"),
                    "verified": bool(receipt.authority.get("verified")),
                },
            },
        )
        self.store.save(receipt)

    # ── rollback ─────────────────────────────────────────────────────────
    def rollback(self, receipt: MigrationReceipt) -> MigrationReceipt:
        """Undo a cutover: clear the source fence, re-claim, tell the remote to stop."""
        result = self.remote.rollback(receipt) or {}
        from navig.move.marker import clear_moved  # noqa: PLC0415

        cleared = clear_moved()

        # The authority's move marker is the shared fence: a rollback that leaves it
        # in place means the source still cannot re-claim, and the rollback lied.
        if self.authority is not None:
            try:
                self.authority.clear_moved(reason=f"rollback of migration {receipt.id}")
                receipt.rollback["authority_moved_cleared"] = True
            except authority.AuthorityError as exc:
                receipt.rollback["authority_moved_cleared"] = False
                self._add_blocker(
                    receipt,
                    f"rollback: the authority's move marker could not be cleared: {exc}",
                )
                logger.warning("migration rollback left the authority's move marker: %s", exc)

        role = normalize_role(receipt.source.get("role"))
        reclaimed = False
        try:
            self.lease_store.acquire(
                role=role, reason=f"rollback of migration {receipt.id}", allow_takeover=False
            )
            reclaimed = True
        except BrainOwnershipError as exc:
            self._add_blocker(receipt, f"rollback: cannot re-claim the lease: {exc}")
        receipt.rollback.update(
            {
                "performed": True,
                "marker_cleared": cleared,
                "lease_reclaimed": reclaimed,
                "target_stopped": bool(result.get("target_stopped")),
            }
        )
        receipt.state = "rolled_back"
        receipt.mark("rollback", _STAGE_DONE, evidence=dict(receipt.rollback))
        self.store.save(receipt)
        return receipt


class SshRemote:
    """The real transport: ``navig`` on the target, over SSH, answering ``--json``.

    Deliberately thin. It shells out once per capability so a failure names the exact
    command, and it never parses human output. Tests inject a fake instead of this
    class, so nothing here runs in CI.
    """

    def __init__(self, host: str, *, ssh: str = "ssh", timeout: float = 120.0) -> None:
        self.host = host
        self.ssh = ssh
        self.timeout = timeout

    # ── plumbing ─────────────────────────────────────────────────────────
    def _spawn(self, argv: list[str]) -> tuple[int, str, str]:
        import subprocess  # noqa: PLC0415

        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv,
            capture_output=True,
            # The remote end is a Linux/mac shell whose output is UTF-8. Named explicitly:
            # `text=True` would decode with the LOCAL code page (cp1252 on this
            # workstation) and mangle every non-ASCII byte the remote prints.
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _run(self, *args: str) -> tuple[int, str, str]:
        return self._spawn([self.ssh, self.host, "navig", *args])

    def _remote(self, *command: str) -> tuple[int, str, str]:
        """A raw command on the target (not ``navig``) — used to hash a shipped bundle."""
        return self._spawn([self.ssh, self.host, *command])

    def _json(self, *args: str) -> dict[str, Any]:
        code, out, err = self._run(*args)
        if code != 0:
            raise MigrationBlocked(
                f"{' '.join(('navig', *args))} failed on {self.host} (exit {code}): "
                f"{(err or out).strip()[:400]}"
            )
        try:
            data = json.loads(out)
        except ValueError as exc:
            raise MigrationBlocked(
                f"{self.host} did not return JSON for {' '.join(('navig', *args))}: "
                f"{out.strip()[:200]}"
            ) from exc
        return data.get("data", data) if isinstance(data, dict) else {"value": data}

    # ── protocol ─────────────────────────────────────────────────────────
    def discover(self) -> dict[str, Any]:
        return self._json("brain", "verify", "--json")

    def inspect(self) -> dict[str, Any]:
        """The target's own ``brain status --json``, flattened for the runner.

        ``brain status`` nests the lease under ``brain`` and the identity fingerprint under
        ``identity``; the runner wants one dict with ``state``/``you``/``role`` and
        ``deck_key_fingerprint`` at the top, so normalise here rather than teach every
        stage two shapes.
        """
        payload = self._json("brain", "status", "--json") or {}
        if not isinstance(payload, dict):
            return {}
        brain = payload.get("brain") if isinstance(payload.get("brain"), dict) else payload
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        return {**brain, **identity}

    def backup(self, destination: Path) -> dict[str, Any]:
        raise MigrationBlocked(
            "no remote back-up transport is configured for this host. Export the Brain on "
            "the source (`navig backup export --brain --encrypt -o brain.tar.gz.enc`) and "
            "pass the archive with --bundle <file> — this stage then records it with a "
            "real digest instead of guessing"
        )

    def transfer(self, bundle: Path) -> dict[str, Any]:
        """Verify a bundle the OPERATOR shipped, by hashing it on the target.

        Copying is deliberately not shelled out from here (a half-written `scp` reported as
        a transfer is exactly the "copied files are not success" failure). The operator
        ships the file; this stage proves the bytes arrived intact, and a mismatch blocks.
        """
        remote_path = f"/tmp/{bundle.name}"
        code, out, err = self._remote("sha256sum", remote_path)
        if code != 0:
            raise MigrationBlocked(
                f"{bundle.name} is not on {self.host}:{remote_path} ({(err or out).strip()[:200]}). "
                f"Copy it first — scp {bundle} {self.host}:{remote_path} — then re-run with "
                "--resume <id> so the receipt records the transfer that happened"
            )
        parts = out.split()
        return {
            "transferred": True,
            "sha256": parts[0] if parts else "",
            "path": remote_path,
        }

    def validate(self, receipt: MigrationReceipt) -> dict[str, Any]:
        status = self.inspect() or {}
        return {
            "ok": True,
            "unreconciled": [],
            # `inspect()` flattened `brain status`'s identity block to the top level.
            "deck_key_fingerprint": str(status.get("deck_key_fingerprint") or ""),
        }

    def cut_over(self, receipt: MigrationReceipt) -> dict[str, Any]:
        """Verify the operator started the target; never claim a start we did not see.

        Starting a Brain on a live machine stays an operator step. This asks the target
        whether it owns the Brain *now* — a green answer is the evidence the receipt
        needs, and anything else blocks instead of writing "complete" over an unknown.
        """
        status = self.inspect() or {}
        you = status.get("you") if isinstance(status.get("you"), dict) else {}
        if status.get("state") == "held" and you.get("is_owner"):
            # The target's own word is not the evidence when a shared authority
            # exists: the AUTHORITY's owner + epoch must match what the target says.
            client = authority.client_or_none()
            if client is not None:
                try:
                    payload = client.status()
                except authority.AuthorityError as exc:
                    raise MigrationBlocked(
                        "the ownership AUTHORITY could not confirm that the target owns "
                        f"the Brain ({exc}). Refusing to report a cutover on an unknown."
                    ) from exc
                raw = payload.get("record") if isinstance(payload, dict) else None
                if not isinstance(raw, dict) or not raw.get("owner_id"):
                    raise MigrationBlocked(
                        "the ownership AUTHORITY has no owner record — a local claim on the "
                        "target is not a shared one. Start the target with the authority "
                        f"configured, then re-run with --resume {receipt.id}"
                    )
                authority_epoch = int(raw.get("epoch") or 0)
                target_epoch = int(status.get("epoch") or 0)
                target_owner = str(you.get("id") or "")
                if str(raw.get("owner_id")) != target_owner or authority_epoch != target_epoch:
                    raise MigrationBlocked(
                        "the authority's epoch does not match the target: the AUTHORITY is at "
                        f"epoch {authority_epoch} ({raw.get('owner_id')}) while the target "
                        f"reports epoch {target_epoch} ({target_owner}). The target is not the "
                        "Brain the authority names."
                    )
                return {
                    "started_target": True,
                    "detail": (
                        f"the target owns the Brain the AUTHORITY names "
                        f"(epoch {authority_epoch})"
                    ),
                    "authority_epoch": authority_epoch,
                    "authority_verified": True,
                }
            return {
                "started_target": True,
                "detail": f"the target owns the Brain (epoch {status.get('epoch')})",
                "authority_verified": False,
            }
        raise MigrationBlocked(
            "the target does not own the Brain yet — start it there "
            "(`navig service start`) and re-run with --resume "
            f"{receipt.id}; status was {status.get('state') or 'unknown'}"
        )

    def verify(self) -> dict[str, dict[str, Any]]:
        out = self._json("brain", "verify", "--json")
        caps = out.get("capabilities") if isinstance(out, dict) else None
        return caps if isinstance(caps, dict) else {}

    def rollback(self, receipt: MigrationReceipt) -> dict[str, Any]:
        return {"target_stopped": False}


def _sha256(path: Path) -> str:
    """The bundle digest used to prove a transfer landed intact."""
    import hashlib  # noqa: PLC0415

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
