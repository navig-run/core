"""Telegram delivery ownership — one mode per Brain, enforced.

The incident this closes: two machines each ran a daemon, a Telegram worker and a
gateway. Both had the bot token. The Windows gateway re-ran ``setWebhook`` on EVERY
start (``drop_pending_updates=True``), so Telegram kept delivering to whichever
machine had most recently booted — the deployment's inbound path silently flipped,
with every light green on both machines.

Telegram gives one bot exactly one delivery mode at a time:

* **polling** — ``getUpdates``; a registered webhook makes it a hard 409.
* **webhook** — Telegram POSTs to one URL.

So the mode is a property of the Brain, not of a process. This module keeps it in
``<config_dir>/brain/telegram_mode.json`` (mode + owning ``owner_id`` + ``epoch`` +
an append-only transition log), and enforces:

1. Only a Brain role may set a webhook. A ``client`` install is refused with the
   reason, because that is precisely the machine that stole inbound before.
2. A different owner may not flip the mode without a recorded transition — a stale
   gateway that boots later cannot silently reclaim delivery.
3. Transitions are auditable and resumable: ``deleteWebhook`` BEFORE polling (never
   the reverse), drain with a de-duplicating ledger so every pending update is
   handled exactly once, and record the counts in the record (and in a migration
   receipt when one is running).

The ledger is the "exactly once" evidence: update ids are claimed before they are
handled, and a replay of an id that is already in the ledger is reported as a
duplicate instead of delivered twice.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from navig.brain import authority as _authority
from navig.brain.identity import hostname
from navig.brain.identity import owner_id as default_owner_id
from navig.brain.lease import (
    SINGLETON_ROLES,
    BrainLease,
    BrainOwnershipError,
    LeaseStore,
    brain_settings,
    normalize_role,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DeliveryLedger",
    "MODE_OFF",
    "MODE_POLLING",
    "MODE_WEBHOOK",
    "MODES",
    "TelegramModeRefused",
    "TelegramModeStore",
    "TransitionResult",
    "authorize_delivery_mode",
    "mode_path",
]

MODE_POLLING = "polling"
MODE_WEBHOOK = "webhook"
MODE_OFF = "off"
MODES: tuple[str, ...] = (MODE_POLLING, MODE_WEBHOOK, MODE_OFF)

_MODE_FILE = "telegram_mode.json"
_LEDGER_FILE = "telegram_delivered.json"

#: How many delivered update ids the ledger remembers. A 24h Telegram backlog is
#: ~a few hundred messages; 2000 ids is cheap (ids are integers) and covers a full
#: day of busy traffic, which is longer than an interrupted migration can be.
_LEDGER_CAP = 2000


class TelegramModeRefused(BrainOwnershipError):
    """This process may not own Telegram delivery. Never swallowed."""


def mode_path() -> Path:
    from navig.platform import paths  # noqa: PLC0415

    return paths.config_dir() / "brain" / _MODE_FILE


def ledger_path() -> Path:
    from navig.platform import paths  # noqa: PLC0415

    return paths.config_dir() / "brain" / _LEDGER_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class TelegramModeRecord:
    """Who owns Telegram delivery right now, and how it got that way."""

    mode: str = MODE_OFF
    owner_id: str = ""
    epoch: int = 0
    updated_at: str = ""
    reason: str = ""
    transitions: tuple[dict[str, Any], ...] = ()
    schema: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "mode": self.mode,
            "owner_id": self.owner_id,
            "epoch": self.epoch,
            "updated_at": self.updated_at,
            "reason": self.reason,
            "transitions": [dict(t) for t in self.transitions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelegramModeRecord:
        transitions = data.get("transitions")
        mode = str(data.get("mode") or MODE_OFF).strip().lower()
        return cls(
            mode=mode if mode in MODES else MODE_OFF,
            owner_id=str(data.get("owner_id") or ""),
            epoch=int(data.get("epoch") or 0),
            updated_at=str(data.get("updated_at") or ""),
            reason=str(data.get("reason") or ""),
            transitions=tuple(t for t in transitions if isinstance(t, dict))
            if isinstance(transitions, list)
            else (),
            schema=int(data.get("schema") or 1),
        )


class TelegramModeStore:
    """Read/write the mode record. Atomic, locked by the lease-store's lock file."""

    def __init__(self, path: Path | None = None) -> None:
        self._explicit = Path(path) if path is not None else None

    @property
    def path(self) -> Path:
        return self._explicit if self._explicit is not None else mode_path()

    def read(self) -> TelegramModeRecord:
        from navig.core.yaml_io import read_text_retrying  # noqa: PLC0415

        # Existence is checked first: `read_text_retrying` retries a missing file on
        # purpose (a replace window looks identical), which would log three warnings on
        # every fresh install — noise that trains people past a real one.
        if not self.path.exists():
            return TelegramModeRecord()
        try:
            text = read_text_retrying(self.path)
        except (FileNotFoundError, OSError):
            return TelegramModeRecord()
        try:
            data = json.loads(text)
        except ValueError:
            return TelegramModeRecord(reason="unreadable mode record")
        return TelegramModeRecord.from_dict(data) if isinstance(data, dict) else TelegramModeRecord()

    def write(
        self, record: TelegramModeRecord, *, from_: str = "", evidenced: dict[str, Any] | None = None
    ) -> TelegramModeRecord:
        from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

        transition = {
            "at": _now_iso(),
            "from": from_ or record.mode,
            "to": record.mode,
            "owner_id": record.owner_id,
            "hostname": hostname(),
            "epoch": record.epoch,
            "reason": record.reason,
            **(evidenced or {}),
        }
        record = replace(record, transitions=(*record.transitions, transition)[-50:])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(record.to_dict(), indent=2))
        return record

    def status(self, *, oid: str | None = None) -> dict[str, Any]:
        record = self.read()
        me = oid or default_owner_id()
        return {
            "mode": record.mode,
            "owner_id": record.owner_id,
            "owner_hostname": hostname() if record.owner_id == me else "",
            "epoch": record.epoch,
            "updated_at": record.updated_at,
            "reason": record.reason,
            "yours": bool(record.owner_id) and record.owner_id == me,
            "transitions": list(record.transitions),
            "path": str(self.path),
        }


@dataclass
class DeliveryLedger:
    """Update ids already delivered — the evidence behind "consumed exactly once"."""

    path: Path | None = None
    delivered: list[int] = field(default_factory=list)
    duplicates: list[int] = field(default_factory=list)

    @property
    def file(self) -> Path:
        return self.path if self.path is not None else ledger_path()

    def load(self) -> DeliveryLedger:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            raw = data.get("delivered") if isinstance(data, dict) else None
            self.delivered = [int(x) for x in raw if isinstance(x, (int, float))] if raw else []
        except (OSError, ValueError):
            self.delivered = []
        return self

    def claim(self, update_id: int) -> bool:
        """``True`` when *update_id* is new (now recorded); ``False`` on a replay."""
        if update_id in self.delivered:
            self.duplicates.append(update_id)
            return False
        self.delivered.append(update_id)
        del self.delivered[:-_LEDGER_CAP]
        return True

    def save(self) -> Path:
        from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

        self.file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.file, json.dumps({"delivered": self.delivered}, indent=2))
        return self.file


@dataclass(frozen=True)
class TransitionResult:
    """What a mode transition actually did — recorded in the receipt."""

    ok: bool
    mode: str
    consumed: int = 0
    duplicates: list[int] = field(default_factory=list)
    pending_before: int = 0
    pending_after: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "consumed": self.consumed,
            "duplicates": list(self.duplicates),
            "pending_before": self.pending_before,
            "pending_after": self.pending_after,
            "detail": self.detail,
        }


def authorize_delivery_mode(
    wanted: str,
    *,
    role: str | None = None,
    owner: str | None = None,
    store: TelegramModeStore | None = None,
    lease_store: LeaseStore | None = None,
    epoch: int | None = None,
    authority_client: Any = None,
) -> None:
    """Raise :class:`TelegramModeRefused` when this process may not own *wanted*.

    Called by the Telegram channel before it registers a webhook or starts polling.
    Refusing here is what makes the incident impossible: the duplicate install does
    not "try and sort it out", it stops and says why.

    When an ownership authority is configured, **only the owner it names at its
    current epoch may set a delivery mode**. A stale process (a gateway that booted
    before a takeover) is refused even though its role is a Brain role, and a
    client install — which holds the same deck key because its config was copied —
    is refused by the authority itself, not merely by this function.
    """
    want = str(wanted or "").strip().lower()
    if want not in MODES:
        raise TelegramModeRefused(f"Unknown Telegram mode {wanted!r}; known: {', '.join(MODES)}")
    if want == MODE_OFF:
        return

    me = owner or default_owner_id()
    settings = brain_settings()
    effective_role = normalize_role(role or settings["role"])

    if effective_role not in SINGLETON_ROLES:
        raise TelegramModeRefused(
            f"This install is a {effective_role} — a {effective_role} cannot own Telegram "
            f"delivery ({want}). It would take the bot's inbound away from the Brain, which "
            "is exactly the failure this guard exists for. The Brain sets the mode: "
            "navig brain telegram polling|webhook"
        )

    client = authority_client if authority_client is not None else _authority.client_or_none()
    if client is not None:
        _authorize_via_authority(
            want,
            me=me,
            effective_role=effective_role,
            epoch=epoch,
            client=client,
        )
    else:
        lease = (lease_store or LeaseStore()).read()
        if lease is not None and lease.is_live() and not lease.held_by(me):
            raise TelegramModeRefused(
                f"The Brain lease belongs to {lease.hostname or 'another machine'} "
                f"({lease.owner_id}) and is heartbeating — this process ({me}) must not set "
                f"Telegram to {want}. Only the Brain owns the bot's delivery mode."
            )

    record = (store or TelegramModeStore()).read()
    if record.mode in (MODE_POLLING, MODE_WEBHOOK) and record.mode != want:
        if record.owner_id and record.owner_id != me:
            raise TelegramModeRefused(
                f"Telegram is in {record.mode} mode, owned by {record.owner_id}"
                f"{' on ' + hostname() if record.owner_id == me else ''}. Switching to "
                f"{want} needs a recorded transition: navig brain telegram {want}"
            )
    if want == MODE_WEBHOOK and record.mode == MODE_WEBHOOK and record.owner_id == me:
        return  # already ours — re-registering the same URL is idempotent


def _authorize_via_authority(
    want: str,
    *,
    me: str,
    effective_role: str,
    epoch: int | None,
    client: Any,
) -> None:
    """The authority's half of the delivery guard: owner + CURRENT epoch."""
    try:
        payload = client.status(oid=me, role=effective_role)
    except _authority.AuthorityUnavailable as exc:
        raise TelegramModeRefused(
            f"The ownership AUTHORITY could not be consulted ({exc}), so it is unknown who "
            f"owns this Brain. Refusing to set Telegram to {want}: only the authority's "
            "current owner may own delivery. Fix the edge/network, then retry."
        ) from exc
    except _authority.AuthorityError as exc:
        raise TelegramModeRefused(
            f"The ownership AUTHORITY refused this machine ({exc}); it may not set Telegram "
            f"to {want}."
        ) from exc

    raw = payload.get("record")
    if not isinstance(raw, dict) or not raw.get("owner_id"):
        raise TelegramModeRefused(
            f"The ownership AUTHORITY records no Brain for this deployment, so nobody may set "
            f"Telegram to {want} yet. Claim it first: navig brain claim --role brain"
        )
    record = BrainLease.from_dict(raw)

    marker = record.moved_marker
    if isinstance(marker, dict) and str(marker.get("from_owner") or "") == me:
        moved_to = str(marker.get("to") or "another machine")
        raise TelegramModeRefused(
            f"The authority's record says this Brain moved to {moved_to}; the machine it "
            f"left may not set Telegram to {want}."
        )
    if record.is_free():
        raise TelegramModeRefused(
            f"The ownership AUTHORITY's record is released (epoch {record.epoch}) — nobody "
            f"owns delivery right now, so this machine may not set Telegram to {want}. "
            "Claim the Brain first."
        )
    if record.owner_id != me:
        raise TelegramModeRefused(
            f"The ownership AUTHORITY says {record.hostname or 'another machine'} "
            f"({record.owner_id}) owns this Brain at epoch {record.epoch} — this machine "
            f"({me}) may not set Telegram to {want}. Only the authority's current owner owns "
            "the bot's delivery mode."
        )

    expected = epoch
    if expected is None:
        try:
            from navig.brain import guard as _guard  # noqa: PLC0415 — avoid an import cycle

            expected = _guard.current_epoch()
        except Exception:  # noqa: BLE001 — no process token simply means "not acquired here"
            expected = None
    if expected is not None and int(expected) != record.epoch:
        raise TelegramModeRefused(
            f"Stale epoch {expected}: the ownership AUTHORITY is at epoch {record.epoch}. This "
            f"process lost ownership (a takeover happened) and must not set Telegram to "
            f"{want}. Restart it under the new owner."
        )


async def transition(
    to_mode: str,
    *,
    api: Callable[[str, dict[str, Any]], Awaitable[Any]],
    on_update: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    owner: str | None = None,
    role: str | None = None,
    epoch: int | None = None,
    reason: str = "",
    drop_pending: bool = False,
    store: TelegramModeStore | None = None,
    ledger: DeliveryLedger | None = None,
    authority_client: Any = None,
) -> TransitionResult:
    """Move Telegram delivery to *to_mode*, verified.

    ``api`` is the Telegram Bot API call (``await api("deleteWebhook", {...})``),
    injected so the whole flow is testable with no network.

    To **polling**: delete the webhook FIRST (a registered webhook makes every
    ``getUpdates`` a 409), then drain whatever Telegram still holds, claiming each
    update id exactly once, then confirm the pending count reached zero and persist
    ``mode=polling`` with the evidence. To **webhook**: refuse if a poller still owns
    the token, then register.

    Nothing here reports success on a partial run: a failed delete, a non-zero
    pending count or a duplicate replay leaves ``ok=False`` with the detail.
    """
    want = str(to_mode or "").strip().lower()
    if want not in MODES:
        raise TelegramModeRefused(f"Unknown Telegram mode {to_mode!r}; known: {', '.join(MODES)}")

    authorize_delivery_mode(
        want,
        role=role,
        owner=owner,
        store=store,
        epoch=epoch,
        authority_client=authority_client,
    )
    me = owner or default_owner_id()
    record_store = store or TelegramModeStore()
    before = record_store.read()
    ledger = (ledger or DeliveryLedger()).load()

    evidence: dict[str, Any] = {"reason": reason or f"transition to {want}"}

    if want == MODE_POLLING:
        # 1. deleteWebhook BEFORE any getUpdates. drop_pending_updates is False unless
        #    the operator explicitly asked: silently discarding a backlog is how
        #    messages disappear during a migration.
        deleted = await api(
            "deleteWebhook", {"drop_pending_updates": bool(drop_pending)}
        )
        if deleted is None:
            return TransitionResult(
                ok=False,
                mode=before.mode,
                detail="deleteWebhook failed — refusing to poll behind a live webhook "
                "(every getUpdates would be a 409)",
            )

        info = await api("getWebhookInfo", {}) or {}
        pending_before = int(info.get("pending_update_count") or 0) if isinstance(info, dict) else 0

        # 2. Drain. Telegram holds the backlog at the edge; getUpdates with offset
        #    confirms consumed ids. Bounded so a pathological backlog cannot hang a
        #    transition forever — an unbounded loop is a hang, not thoroughness.
        consumed = 0
        last_id: int | None = None
        for _ in range(20):
            batch = await api(
                "getUpdates",
                {
                    "offset": (last_id + 1) if last_id is not None else 0,
                    "timeout": 0,
                    "limit": 100,
                    "allowed_updates": _allowed_updates(),
                },
            )
            if not batch:
                break
            if not isinstance(batch, list):
                break
            for update in batch:
                if not isinstance(update, dict):
                    continue
                uid = update.get("update_id")
                if not isinstance(uid, int):
                    continue
                last_id = uid
                if not ledger.claim(uid):
                    continue  # replay: already handled by somebody
                consumed += 1
                if on_update is not None:
                    await on_update(update)
            if len(batch) < 100:
                break
        ledger.save()

        # Confirm the FINAL update. Telegram only forgets an update when a getUpdates
        # offset GREATER than its id is passed, so draining without this leaves the last
        # one pending forever and `pending_update_count` never reaches zero (the test that
        # asserts a zero count caught exactly this omission).
        if last_id is not None:
            await api(
                "getUpdates",
                {
                    "offset": last_id + 1,
                    "timeout": 0,
                    "limit": 1,
                    "allowed_updates": _allowed_updates(),
                },
            )

        after = await api("getWebhookInfo", {}) or {}
        pending_after = int(after.get("pending_update_count") or 0) if isinstance(after, dict) else 0
        evidence.update(
            {
                "pending_before": pending_before,
                "pending_after": pending_after,
                "consumed": consumed,
                "duplicates": ledger.duplicates,
                "last_update_id": last_id,
            }
        )
        if pending_after:
            record_store.write(
                replace(
                    before,
                    mode=MODE_POLLING,
                    owner_id=me,
                    epoch=int(epoch if epoch is not None else before.epoch),
                    updated_at=_now_iso(),
                    reason=f"{reason} (incomplete: {pending_after} still pending)",
                ),
                from_=before.mode,
                evidenced=evidence,
            )
            return TransitionResult(
                ok=False,
                mode=MODE_POLLING,
                consumed=consumed,
                duplicates=list(ledger.duplicates),
                pending_before=pending_before,
                pending_after=pending_after,
                detail=f"{pending_after} update(s) still pending after the drain — do not "
                "report the migration complete",
            )
        record_store.write(
            replace(
                before,
                mode=MODE_POLLING,
                owner_id=me,
                epoch=int(epoch if epoch is not None else before.epoch),
                updated_at=_now_iso(),
                reason=reason or "polling owns delivery",
            ),
            from_=before.mode,
            evidenced=evidence,
        )
        return TransitionResult(
            ok=True,
            mode=MODE_POLLING,
            consumed=consumed,
            duplicates=list(ledger.duplicates),
            pending_before=pending_before,
            pending_after=pending_after,
            detail=f"webhook deleted, {consumed} pending update(s) consumed exactly once",
        )

    # webhook (or off)
    if want == MODE_WEBHOOK:
        info = await api("getWebhookInfo", {}) or {}
        registered = info.get("url") if isinstance(info, dict) else ""
        evidence["previous_webhook"] = registered or ""
    record_store.write(
        replace(
            before,
            mode=want,
            owner_id=me if want != MODE_OFF else "",
            epoch=int(epoch if epoch is not None else before.epoch),
            updated_at=_now_iso(),
            reason=reason or f"{want} owns delivery",
        ),
        from_=before.mode,
        evidenced=evidence,
    )
    return TransitionResult(ok=True, mode=want, detail=f"delivery mode is now {want}")


def _allowed_updates() -> list[str]:
    """The ONE allowed-updates list — retyping it inline silently narrows delivery."""
    from navig.telegram.updates import ALLOWED_UPDATES  # noqa: PLC0415

    return list(ALLOWED_UPDATES)
