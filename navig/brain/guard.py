"""Singleton-service guards: refuse to run a second Brain.

Every entry point that owns a *singleton* — the daemon, the gateway, the Telegram
worker, the cron tick — calls :func:`require_ownership` before it starts serving.
If this machine is not the Brain, or another machine is heartbeating the lease, the
service refuses with one clear sentence and a distinct exit code
(:data:`navig.brain.lease.EXIT_OWNERSHIP_REFUSED`).

Three properties matter more than the message:

1. **Refusing is safe, starting twice is not.** The guard never touches the lease
   when it refuses — a Windows scheduled task that fires again after the Brain moved
   must not re-claim (``tests/brain/test_ownership_guards.py`` pins that).
2. **The role gate is separate from the lease.** A ``client`` or ``executor`` install
   may not run a singleton even when the lease is free: it does not own an identity.
3. **The epoch is carried forward.** A successful acquire stores
   ``(owner_id, epoch)`` for this process; every fenced write presents it.

Nothing here is a network check *when no authority is configured*. When one is
(the Lighthouse edge — see :mod:`navig.brain.authority`), the singleton start
consults it and **fails closed** (exit 77) if it cannot be reached: a partition
is not a licence to promote this machine. The local lease file is then only a
cache of what the authority last said.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from navig.brain import authority as _authority
from navig.brain.authority import AuthorityError, AuthorityUnavailable
from navig.brain.identity import hostname
from navig.brain.identity import owner_id as default_owner_id
from navig.brain.lease import (
    EXIT_OWNERSHIP_REFUSED,
    ROLE_ALL_IN_ONE,
    ROLE_BRAIN,
    ROLE_CLIENT,
    SINGLETON_ROLES,
    BrainLease,
    BrainOwnershipError,
    LeaseStore,
    OwnershipConflict,
    StaleEpoch,
    brain_settings,
    normalize_role,
)

logger = logging.getLogger(__name__)

__all__ = [
    "EXIT_OWNERSHIP_REFUSED",
    "OwnershipDecision",
    "OwnershipRefused",
    "REFUSAL_SERVICES",
    "check",
    "current_epoch",
    "current_owner",
    "fenced_write",
    "is_owner",
    "refresh",
    "release_process_lease",
    "require_ownership",
    "reset_process_lease",
    "services_for_role",
]

#: Every singleton this module knows how to guard, and the human name it uses.
REFUSAL_SERVICES: dict[str, str] = {
    "daemon": "the NAVIG daemon",
    "gateway": "the NAVIG gateway",
    "telegram": "the Telegram bot",
    "scheduler": "the scheduler / cron tick",
}

#: Process-wide token: ``(owner_id, epoch)`` after a successful guard.
_TOKEN_LOCK = threading.Lock()
_TOKEN: dict[str, Any] = {"owner_id": None, "epoch": None, "role": None, "service": None}


def services_for_role(role: str) -> bool:
    """May a lease with *role* run singletons?"""
    return normalize_role(role) in SINGLETON_ROLES


@dataclass(frozen=True)
class OwnershipDecision:
    """The verdict. ``allowed=False`` always carries a message worth printing."""

    allowed: bool
    service: str
    role: str
    owner_id: str
    lease: BrainLease | None
    message: str
    exit_code: int = 0
    #: ``remote`` (the Lighthouse authority answered), ``local`` (single-machine
    #: install, no authority configured) or ``unconfigured`` (required but absent).
    authority: str = "local"

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "service": self.service,
            "role": self.role,
            "you": self.owner_id,
            "epoch": self.lease.epoch if self.lease else 0,
            "message": self.message,
            "exit_code": self.exit_code,
            "authority": self.authority,
        }


class OwnershipRefused(BrainOwnershipError):
    """Raised by :func:`require_ownership`. ``exit_code`` is 77."""

    def __init__(self, decision: OwnershipDecision) -> None:
        super().__init__(decision.message)
        self.decision = decision
        self.exit_code = decision.exit_code or EXIT_OWNERSHIP_REFUSED


def _moved_message() -> str | None:
    """This NAVIG was moved away — the strongest refusal there is."""
    try:
        from navig.move.marker import moved_message, read_moved  # noqa: PLC0415

        info = read_moved()
    except Exception:  # noqa: BLE001 — an unreadable marker must not crash a boot
        return None
    return moved_message(info) if info is not None else None


def _authority_moved_message(marker: dict[str, Any] | None) -> str | None:
    """The authority's record says this Brain moved — the source must not re-claim."""
    if not isinstance(marker, dict):
        return None
    if str(marker.get("from_owner") or "") != default_owner_id():
        return None
    to = str(marker.get("to") or "another machine")
    migration = str(marker.get("migration") or "")
    return (
        f"This Brain moved to {to}"
        + (f" (migration {migration})" if migration else "")
        + " according to the ownership AUTHORITY, and the machine it left must not "
        "re-claim it. If that migration must be undone, roll it back explicitly "
        "(`navig brain migrate --resume <id>` then its rollback), which clears the "
        "authority's move marker."
    )


def _refuse(
    service: str,
    role: str,
    oid: str,
    lease: BrainLease | None,
    message: str,
    *,
    authority: str = "local",
):
    return OwnershipDecision(
        allowed=False,
        service=service,
        role=role,
        owner_id=oid,
        lease=lease,
        message=message,
        exit_code=EXIT_OWNERSHIP_REFUSED,
        authority=authority,
    )


def check(
    *,
    service: str,
    store: LeaseStore | None = None,
    role: str | None = None,
    owner: str | None = None,
    acquire: bool = False,
    authority_client: Any = None,
) -> OwnershipDecision:
    """Decide whether *service* may run here, without raising.

    ``acquire=True`` performs the claim/renew when the verdict is "yes"; ``False``
    only inspects (``navig brain status``, the deck route, ``navig doctor``).

    When a shared authority is configured, the decision is the AUTHORITY's: the
    local lease file is a cache and is never substituted for an answer. If the
    authority cannot be consulted the decision is a refusal (exit 77) — fail
    closed, no self-promotion. With no authority configured (single-machine
    install) the local record is used, and status says ``authority: local``.
    """
    label = REFUSAL_SERVICES.get(service, service)
    oid = owner or default_owner_id()
    settings = brain_settings()
    want_role = normalize_role(role or settings["role"])

    moved = _moved_message()
    if moved is not None:
        return _refuse(service, want_role, oid, None, moved)

    if want_role not in SINGLETON_ROLES:
        return _refuse(
            service,
            want_role,
            oid,
            None,
            f"This install is a {want_role}, so it cannot run {label}. A {want_role} has "
            "no brain identity — it connects to a Brain instead. If this machine SHOULD "
            "own the Brain: navig brain claim --role brain --reason <why>",
        )

    client = authority_client if authority_client is not None else _authority.client_or_none()
    if client is None and _authority.required():
        return _refuse(
            service,
            want_role,
            oid,
            None,
            "brain.authority_required is on, but this install has no shared authority "
            "configured. Set brain.authority_url (or cloud.lighthouse_url) and a "
            f"deck.api_key, or turn brain.authority_required off. {label} will not start "
            "without an authoritative owner record.",
            authority="unconfigured",
        )
    active = _authority.lease_store(store, client=client)
    mode = "remote" if isinstance(active, _authority.AuthorityLeaseStore) else "local"

    try:
        lease = active.acquire(oid=oid, role=want_role) if acquire else active.read()
    except AuthorityUnavailable as exc:
        # The authority is configured but said nothing. Refusing is the only safe
        # answer: silence is not evidence that no other machine is the Brain.
        return _refuse(
            service,
            want_role,
            oid,
            _cached_lease_safely(),
            f"{exc} {label} refuses to start (exit {EXIT_OWNERSHIP_REFUSED}). Nothing "
            "was promoted locally.",
            authority=mode,
        )
    except OwnershipConflict as exc:
        return _refuse(service, want_role, oid, _safe_read(active), str(exc), authority=mode)
    except BrainOwnershipError as exc:
        return _refuse(service, want_role, oid, None, str(exc), authority=mode)
    except (OSError, RuntimeError) as exc:
        # The config dir cannot be resolved (a broken HOME, a read-only system-service
        # home). The lease has nowhere to live, so ownership cannot be enforced by this
        # process AT ALL — and refusing here would block a boot that is otherwise fine.
        # Same rule the moved marker already follows: an unresolvable home is not
        # evidence of another Brain. It is logged, never silent.
        logger.warning(
            "brain ownership could not be enforced for %s (%s: %s) — starting without a "
            "lease; no config dir resolved, so there is nowhere to record ownership",
            service,
            type(exc).__name__,
            exc,
        )
        return OwnershipDecision(
            allowed=True,
            service=service,
            role=want_role,
            owner_id=oid,
            lease=None,
            message=f"ownership not enforced: {type(exc).__name__}: {exc}",
            authority=mode,
        )

    authority_moved = _authority_moved_message(
        lease.moved_marker if lease is not None else None
    )
    if authority_moved is not None:
        return _refuse(service, want_role, oid, lease, authority_moved, authority=mode)

    if not acquire:
        # Inspect-only: the verdict mirrors what acquire WOULD do.
        if lease is None or lease.is_free() or lease.held_by(oid):
            return OwnershipDecision(
                allowed=True,
                service=service,
                role=want_role,
                owner_id=oid,
                lease=lease,
                message="this machine may run the Brain",
                authority=mode,
            )
        if lease.is_live():
            return _refuse(
                service,
                want_role,
                oid,
                lease,
                f"The Brain is owned by {lease.hostname or 'another machine'} "
                f"({lease.owner_id}) and is heartbeating. {label} would refuse to start "
                "here.",
                authority=mode,
            )
        return _refuse(
            service,
            want_role,
            oid,
            lease,
            f"The Brain lease of {lease.hostname or 'another machine'} "
            f"({lease.owner_id}) has expired. {label} will not start until it is taken "
            "over explicitly (navig brain takeover).",
            authority=mode,
        )

    if lease is None:  # pragma: no cover — acquire() either returns a lease or raises
        return _refuse(service, want_role, oid, None, "the lease could not be acquired")
    _remember(lease, want_role, service)
    return OwnershipDecision(
        allowed=True,
        service=service,
        role=want_role,
        owner_id=oid,
        lease=lease,
        message=f"this machine owns the Brain (epoch {lease.epoch}, role {want_role})",
        authority=mode,
    )


def _safe_read(store: Any) -> BrainLease | None:
    """Best-effort record for a refusal message — never raises, never promotes."""
    try:
        return store.read()
    except Exception:  # noqa: BLE001 — a refusal's context must not fail the refusal
        return None


def _cached_lease_safely() -> BrainLease | None:
    try:
        return _authority.cached_lease()
    except Exception:  # noqa: BLE001
        return None


def require_ownership(
    *,
    service: str,
    store: LeaseStore | None = None,
    role: str | None = None,
    owner: str | None = None,
    authority_client: Any = None,
) -> BrainLease | None:
    """Claim/renew and return the lease, or raise :class:`OwnershipRefused`.

    ``None`` is returned in the ONE degraded case: no config dir resolves, so ownership
    cannot be recorded at all. Callers treat that as "run unfenced, and say so" —
    refusing would block a boot that has nowhere to write a lease either way.
    """
    decision = check(
        service=service,
        store=store,
        role=role,
        owner=owner,
        acquire=True,
        authority_client=authority_client,
    )
    if not decision.allowed:
        raise OwnershipRefused(decision)
    return decision.lease


# ── the process token (what fenced writers present) ─────────────────────────


def _remember(lease: BrainLease, role: str, service: str) -> None:
    with _TOKEN_LOCK:
        _TOKEN.update(
            {
                "owner_id": lease.owner_id,
                "epoch": lease.epoch,
                "role": role,
                "service": service,
                "at": time.time(),
            }
        )


def reset_process_lease() -> None:
    """Forget the token — used by tests and by a service that stopped on purpose."""
    with _TOKEN_LOCK:
        _TOKEN.update({"owner_id": None, "epoch": None, "role": None, "service": None})


def current_owner() -> str | None:
    with _TOKEN_LOCK:
        return _TOKEN.get("owner_id")


def current_epoch() -> int | None:
    """The epoch this process acquired — what every singleton write presents."""
    with _TOKEN_LOCK:
        return _TOKEN.get("epoch")


def is_owner(*, store: LeaseStore | None = None) -> bool:
    """Is this process still the owner *right now* (lease still ours and unexpired)?"""
    store = _authority.lease_store(store)
    try:
        lease = store.read()
    except BrainOwnershipError as exc:
        # An authority that cannot be consulted is "not the owner", never "assume yes".
        if isinstance(exc, AuthorityError):
            logger.warning("brain ownership could not be confirmed: %s", exc)
        return False
    if lease is None or lease.is_free():
        return False
    return lease.held_by(default_owner_id()) and not lease.is_expired()


def refresh(*, store: LeaseStore | None = None, ttl: int | None = None) -> BrainLease:
    """Heartbeat this process's own lease. Raises :class:`StaleEpoch` after a takeover."""
    store = _authority.lease_store(store)
    oid = current_owner() or default_owner_id()
    return store.renew(oid=oid, epoch=current_epoch(), ttl=ttl)


def release_process_lease(
    *, store: LeaseStore | None = None, reason: str = "service stopped"
) -> bool:
    """Release on a clean stop. Never raises — a stop path must not fail loudly."""
    store = _authority.lease_store(store)
    oid = current_owner() or default_owner_id()
    try:
        store.release(oid=oid, epoch=current_epoch(), reason=reason)
    except BrainOwnershipError as exc:
        logger.info("brain lease not released on stop: %s", exc)
        return False
    finally:
        reset_process_lease()
    return True


def fenced_write(path, text: str, *, who: str = "singleton"):
    """Write singleton state only while this process still owns the lease.

    The one call a singleton uses instead of a bare atomic write. It presents the
    epoch captured by :func:`require_ownership`; if a takeover happened in between,
    the write is refused (:class:`StaleEpoch`) rather than corrupting the new
    Brain's state. When the authority cannot be consulted the write is refused too
    (:class:`~navig.brain.authority.AuthorityUnavailable`) — fail closed.
    """
    from pathlib import Path  # noqa: PLC0415

    return _authority.lease_store().fenced_write(
        Path(path), text, epoch=current_epoch(), who=who
    )


def write_allowed(*, who: str = "singleton", store: LeaseStore | None = None) -> bool:
    """Non-raising fence check for a write path that must degrade, not crash.

    A scheduler that discovers it has been fenced must refuse the WRITE and keep
    telling the operator, not take the process down with it. An authority that
    cannot be asked means the epoch cannot be confirmed, so the write is refused.
    """
    epoch = current_epoch()
    if epoch is None:
        return True  # no acquired lease in this process (CLI/tests): unfenced caller
    try:
        _authority.lease_store(store).assert_epoch(epoch, who=who)
    except StaleEpoch:
        return False
    except AuthorityError as exc:
        logger.warning("refusing a write (%s): the ownership authority is unreachable: %s", who, exc)
        return False
    return True


def assert_write_allowed(*, who: str = "singleton", store: LeaseStore | None = None) -> None:
    """:func:`write_allowed`, but raises :class:`StaleEpoch` — for strict callers."""
    epoch = current_epoch()
    if epoch is None:
        return
    _authority.lease_store(store).assert_epoch(epoch, who=who)


def refusal_exit(decision: OwnershipDecision) -> int:
    """The exit code a CLI/entry point should exit with for *decision*."""
    return 0 if decision.allowed else (decision.exit_code or EXIT_OWNERSHIP_REFUSED)


__all__ += [
    "ROLE_ALL_IN_ONE",
    "ROLE_BRAIN",
    "ROLE_CLIENT",
    "assert_write_allowed",
    "hostname",
    "refusal_exit",
    "write_allowed",
]
