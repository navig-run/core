"""The Brain ownership record — its shape, operations and fencing rules.

The model in one paragraph: a *deployment* (one Telegram bot, one deck key, one
vault, one scheduler) must have exactly **one** Brain. Every install declares a
``role`` and the machine that runs the singletons (daemon, gateway, Telegram,
cron) must hold the **ownership record** for it.

Where the record lives is the point of this module's companion,
:mod:`navig.brain.authority`: when a Lighthouse edge is configured, the
AUTHORITATIVE record is the ``OwnershipRecord`` Durable Object — one per tenant,
reached over authenticated HTTP, with an atomic compare-and-set on ``epoch``.
This module supplies the record shape (:class:`BrainLease`), the operations and
the fencing semantics, and :class:`LeaseStore` is the **single-machine** record
plus the local **cache/mirror** of the authority's answer. A local file is never
the source of truth while an authority is configured, and a guard never treats
an unreachable authority as permission to promote.

Semantics (deliberately boring, and identical locally and in the authority):

``acquire``  take a free record, or keep one we already hold (idempotent). Refuses a
             record that a *different* machine holds and is still heartbeating.
``renew``    heartbeat. Must present the current ``epoch``; a stale writer is refused.
``release``  give it up cleanly (epoch stays, so a stale writer is still fenced).
``takeover`` explicit, audited displacement: bumps the ``epoch`` and appends to
             ``takeovers[]``. Allowed when the holder's record has expired, or with
             ``force`` + the expected current owner (so a race loses safely).
``status``   who owns it, whether it is live, and what this machine may do.

**Fencing, because a lease is not a lock.** Heartbeats are liveness, not
correctness: a holder that is merely slow must not be silently replaced (that is a
split brain), and a process that lost ownership must not keep writing. So the
``epoch`` is a fencing token: it only ever increases, and every write a singleton
service performs presents the epoch it acquired. A write carrying an older epoch
is refused — ``LeaseStore.fenced_write`` / :func:`navig.brain.guard.writer`.

Defaults are the safe ones: promotion is **manual**. ``brain.auto_promote`` must be
turned on explicitly before an expired record is taken over automatically.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator

from navig.brain.identity import hostname
from navig.brain.identity import owner_id as default_owner_id

logger = logging.getLogger(__name__)

__all__ = [
    "BrainLease",
    "BrainOwnershipError",
    "LeaseStore",
    "OwnershipConflict",
    "ROLES",
    "ROLE_ALL_IN_ONE",
    "ROLE_BRAIN",
    "ROLE_CLIENT",
    "ROLE_EXECUTOR",
    "SINGLETON_ROLES",
    "StaleEpoch",
    "brain_settings",
    "normalize_role",
    "lease_path",
    "status_from_lease",
]

# ── roles ────────────────────────────────────────────────────────────────────
ROLE_BRAIN = "brain"
ROLE_CLIENT = "client"
ROLE_EXECUTOR = "executor"
ROLE_ALL_IN_ONE = "all-in-one"

#: Every role a lease may carry. ``executor`` is a machine that runs work (builds,
#: agents, a browser) FOR a Brain but owns no singleton and no identity.
ROLES: tuple[str, ...] = (ROLE_BRAIN, ROLE_CLIENT, ROLE_EXECUTOR, ROLE_ALL_IN_ONE)

#: Roles allowed to run a singleton service (daemon / gateway / Telegram / cron).
SINGLETON_ROLES = frozenset({ROLE_BRAIN, ROLE_ALL_IN_ONE})

#: A lease that is this old (since its last heartbeat) is EXPIRED — which permits an
#: explicit takeover. It never permits an automatic one unless auto-promotion is on.
DEFAULT_TTL_SECONDS = 300

#: Exit code for "this process refuses to start because it is not the Brain".
#: Distinct from 1 so a launcher/provider can tell a refusal from a crash
#: (sysexits EX_NOPERM).
EXIT_OWNERSHIP_REFUSED = 77

_SCHEMA = 1
_LEASE_FILE = "lease.json"


class BrainOwnershipError(RuntimeError):
    """Base class for every ownership refusal. Carries the exit code a CLI uses."""

    exit_code = EXIT_OWNERSHIP_REFUSED


class OwnershipConflict(BrainOwnershipError):
    """Another machine holds a live lease — the caller must not proceed."""


class StaleEpoch(BrainOwnershipError):
    """A writer presented an epoch that is no longer current — it lost ownership."""


def normalize_role(value: Any) -> str:
    """Map anything an operator/config might spell to one of :data:`ROLES`.

    Legacy spellings are kept working: ``onboarding.role: node`` (a joined device) is
    a ``client`` and ``brain`` a ``brain``; empty/unknown means ``all-in-one``, the
    historical behaviour of a single local install that runs everything.
    """
    text = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "": ROLE_ALL_IN_ONE,
        "brain": ROLE_BRAIN,
        "server": ROLE_BRAIN,
        "always-on": ROLE_BRAIN,
        "all-in-one": ROLE_ALL_IN_ONE,
        "allinone": ROLE_ALL_IN_ONE,
        "local": ROLE_ALL_IN_ONE,
        "node": ROLE_CLIENT,
        "client": ROLE_CLIENT,
        "executor": ROLE_EXECUTOR,
        "worker": ROLE_EXECUTOR,
    }
    return aliases.get(text, ROLE_ALL_IN_ONE)


# ── settings ─────────────────────────────────────────────────────────────────


def _env_flag(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return None
    from navig.core.coerce import coerce_bool  # noqa: PLC0415 — keep import light

    return coerce_bool(raw, default=False)


def brain_settings() -> dict[str, Any]:
    """``brain.*`` settings, env-first.

    Read straight from ``<config_dir>/config.yaml`` rather than through
    ``ConfigManager``: this is called on every boot and from every guard, and
    ``navig.core.Config`` is a process-wide singleton that can describe the wrong
    install. A missing/garbled file means "defaults", which for a fresh local
    install is ``all-in-one`` — the historical behaviour, so nothing needs
    configuring to keep working.

    Environment wins, which is how a supervised service pins its role and how the
    tests isolate themselves.

    Keys: ``role``, ``ttl`` (seconds), ``auto_promote`` (bool, default **False**),
    ``lease_path`` (a shared path for a deployment whose machines can see one file),
    and the authority block: ``authority_url`` (the Lighthouse edge — defaults to
    ``cloud.lighthouse_url``), ``authority_required`` (refuse to run a singleton
    without a reachable authority), ``authority_timeout``, ``authority_retries``
    and ``authority_cache_ttl`` (how long a fetched epoch may satisfy a fence
    check before it is re-read).
    """
    from navig.platform import paths  # noqa: PLC0415 — honours NAVIG_CONFIG_DIR

    section: dict[str, Any] = {}
    onboarding: dict[str, Any] = {}
    cloud: dict[str, Any] = {}
    try:
        import yaml  # noqa: PLC0415

        raw = yaml.safe_load(
            (paths.config_dir() / "config.yaml").read_text(encoding="utf-8")
        )
        if isinstance(raw, dict):
            if isinstance(raw.get("brain"), dict):
                section = dict(raw["brain"])
            if isinstance(raw.get("onboarding"), dict):
                onboarding = dict(raw["onboarding"])
            if isinstance(raw.get("cloud"), dict):
                cloud = dict(raw["cloud"])
    except Exception:  # noqa: BLE001 — a settings read must never block a boot
        section = {}

    # ``onboarding.role`` is the historical place a join wrote the device's role.
    if not section.get("role") and onboarding.get("role"):
        section["role"] = onboarding["role"]

    role = normalize_role(os.environ.get("NAVIG_BRAIN_ROLE") or section.get("role"))

    ttl_raw = os.environ.get("NAVIG_BRAIN_TTL") or section.get("ttl") or DEFAULT_TTL_SECONDS
    try:
        ttl = max(30, int(float(ttl_raw)))
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL_SECONDS

    auto = _env_flag("NAVIG_BRAIN_AUTO_PROMOTE")
    if auto is None:
        from navig.core.coerce import coerce_bool  # noqa: PLC0415

        auto = coerce_bool(section.get("auto_promote", False), default=False)

    shared = os.environ.get("NAVIG_BRAIN_LEASE_PATH") or section.get("lease_path") or ""

    # The authority is the Lighthouse edge the deployment already has: its
    # ``cloud.lighthouse_url`` is where the deck, the Telegram webhook and the
    # uplink point. ``brain.authority_url`` overrides it for a self-hosted edge.
    authority_url = (
        os.environ.get("NAVIG_BRAIN_AUTHORITY_URL")
        or section.get("authority_url")
        or cloud.get("lighthouse_url")
        or ""
    )
    authority_required = _env_flag("NAVIG_BRAIN_AUTHORITY_REQUIRE")
    if authority_required is None:
        from navig.core.coerce import coerce_bool  # noqa: PLC0415

        authority_required = coerce_bool(section.get("authority_required", False), default=False)

    def _seconds(name: str, env: str, default: float) -> float:
        raw_value = os.environ.get(env) or section.get(name) or default
        try:
            return max(0.5, float(raw_value))
        except (TypeError, ValueError):
            return default

    return {
        "role": role,
        "ttl": ttl,
        "auto_promote": bool(auto),
        "lease_path": str(shared).strip(),
        "authority_url": str(authority_url).strip().rstrip("/"),
        "authority_required": bool(authority_required),
        "authority_timeout": _seconds("authority_timeout", "NAVIG_BRAIN_AUTHORITY_TIMEOUT", 3.0),
        "authority_retries": int(_seconds("authority_retries", "NAVIG_BRAIN_AUTHORITY_RETRIES", 1)),
        "authority_cache_ttl": _seconds(
            "authority_cache_ttl", "NAVIG_BRAIN_AUTHORITY_CACHE_TTL", 10.0
        ),
    }


def lease_path() -> Path:
    """``<config_dir>/brain/lease.json`` — or ``brain.lease_path`` when configured.

    A shared path (a synced folder, an SMB mount) is what makes the lease enforce
    ACROSS machines without a network dependency; by default it is local, and the
    cross-machine guarantee comes from the migration marker + receipt
    (see ``docs/architecture/brain-ownership.md``).
    """
    configured = brain_settings().get("lease_path") or ""
    if configured:
        return Path(configured).expanduser()
    from navig.platform import paths  # noqa: PLC0415

    return paths.config_dir() / "brain" / _LEASE_FILE


# ── the record ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BrainLease:
    """One lease record. Timestamps are unix floats; the CLI formats them."""

    owner_id: str
    role: str
    epoch: int
    acquired_at: float
    heartbeat_at: float
    ttl: int = DEFAULT_TTL_SECONDS
    hostname: str = ""
    reason: str = ""
    takeovers: tuple[dict[str, Any], ...] = ()
    released_at: float | None = None
    schema: int = _SCHEMA
    #: Set by a migration IN THE AUTHORITY record (never by the local file): the
    #: machine this Brain moved away from may not re-claim it. Mirrored here so
    #: `status`, the deck and the guards read one shape.
    moved_marker: dict[str, Any] | None = None
    #: Server time of the last authority write; 0 on a purely local lease.
    updated_at: float = 0.0

    # ── state ────────────────────────────────────────────────────────────
    def is_free(self) -> bool:
        """Released, or never meaningfully held."""
        return self.released_at is not None

    def is_expired(self, now: float | None = None) -> bool:
        """No heartbeat inside the TTL. Expiry permits a MANUAL takeover."""
        if self.is_free():
            return True
        return (now if now is not None else time.time()) > self.heartbeat_at + self.ttl

    def is_live(self, now: float | None = None) -> bool:
        return not self.is_free() and not self.is_expired(now)

    def held_by(self, oid: str) -> bool:
        return self.owner_id == oid

    def expires_in(self, now: float | None = None) -> int:
        if self.is_free():
            return 0
        now = now if now is not None else time.time()
        return max(0, int(self.heartbeat_at + self.ttl - now))

    def state(self, now: float | None = None) -> str:
        if self.is_free():
            return "released"
        return "expired" if self.is_expired(now) else "held"

    # ── serialisation ────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "owner_id": self.owner_id,
            "role": self.role,
            "epoch": self.epoch,
            "acquired_at": self.acquired_at,
            "heartbeat_at": self.heartbeat_at,
            "ttl": self.ttl,
            "hostname": self.hostname,
            "reason": self.reason,
            "takeovers": [dict(t) for t in self.takeovers],
            "released_at": self.released_at,
            "moved_marker": dict(self.moved_marker) if self.moved_marker else None,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BrainLease:
        """Parse leniently: an older/partial record still yields a usable lease."""
        takeovers = data.get("takeovers")
        moved = data.get("moved_marker")
        return cls(
            owner_id=str(data.get("owner_id") or ""),
            role=normalize_role(data.get("role")),
            epoch=int(data.get("epoch") or 1),
            acquired_at=float(data.get("acquired_at") or 0.0),
            heartbeat_at=float(data.get("heartbeat_at") or 0.0),
            ttl=int(data.get("ttl") or DEFAULT_TTL_SECONDS),
            hostname=str(data.get("hostname") or ""),
            reason=str(data.get("reason") or ""),
            takeovers=tuple(t for t in takeovers if isinstance(t, dict))
            if isinstance(takeovers, list)
            else (),
            released_at=float(data["released_at"]) if data.get("released_at") else None,
            schema=int(data.get("schema") or _SCHEMA),
            moved_marker=dict(moved) if isinstance(moved, dict) and moved.get("to") else None,
            updated_at=float(data.get("updated_at") or 0.0),
        )

    def describe(self, now: float | None = None) -> str:
        state = self.state(now)
        who = f"{self.hostname or 'unknown-host'} ({self.owner_id})"
        if state == "released":
            return f"free (last held by {who}, epoch {self.epoch})"
        return (
            f"{who}, role {self.role}, epoch {self.epoch}, {state}, "
            f"heartbeat {self.expires_in(now)}s of TTL left"
        )


# ── the store ────────────────────────────────────────────────────────────────


def status_from_lease(
    lease: BrainLease | None,
    *,
    oid: str,
    settings: dict[str, Any],
    path: str,
    now: float | None = None,
    authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The ONE status document, for a local lease or an authority record.

    Shared by :class:`LeaseStore` (local mode) and
    :class:`navig.brain.authority.AuthorityLeaseStore` (remote mode), so the CLI,
    the deck API and the OS card never see two shapes. ``authority`` names where
    the record came from — a status view that cannot say that is the "green light
    over an unknown" this whole area exists to prevent.
    """
    block = (
        authority
        if authority is not None
        else {"mode": "local", "url": "", "reachable": None}
    )
    role = settings["role"]
    base_settings = {
        "ttl": settings["ttl"],
        "auto_promote": settings["auto_promote"],
        "role_source": "env" if os.environ.get("NAVIG_BRAIN_ROLE") else "config",
    }
    if lease is None:
        return {
            "state": "free",
            "owner": None,
            "you": {"id": oid, "hostname": hostname(), "role": role, "is_owner": False},
            "epoch": 0,
            "lease": None,
            "settings": base_settings,
            "can_claim": role in SINGLETON_ROLES,
            "can_takeover": False,
            "summary": "free — nobody owns this Brain",
            "path": path,
            "authority": block,
        }
    return {
        "state": lease.state(now),
        "owner": {
            "id": lease.owner_id,
            "hostname": lease.hostname,
            "role": lease.role,
            "since": lease.acquired_at,
            "heartbeat_at": lease.heartbeat_at,
            "expires_in_s": lease.expires_in(now),
        },
        "you": {
            "id": oid,
            "hostname": hostname(),
            "role": role,
            "is_owner": lease.held_by(oid) and not lease.is_free(),
        },
        "epoch": lease.epoch,
        "lease": lease.to_dict(),
        "settings": base_settings,
        "can_claim": role in SINGLETON_ROLES and (lease.is_free() or lease.held_by(oid)),
        # `can_claim` means "acquire would succeed right now". A takeover is the
        # other door: permitted while expired, always audited, never automatic.
        "can_takeover": role in SINGLETON_ROLES
        and not lease.held_by(oid)
        and not lease.is_live(now),
        "summary": lease.describe(now),
        "path": path,
        "authority": block,
    }


_lock_state = threading.local()

#: Serializes the critical section between THREADS of one process. The file lock
#: below covers other processes; on Windows a byte-range lock taken from a second
#: handle of the same process is not reliably exclusive, so both are needed to make
#: "two promotions race, exactly one wins" true within a single daemon process too.
_PROCESS_LOCK = threading.RLock()


@contextmanager
def _lease_lock(path: Path) -> Iterator[None]:
    """Cross-process lock around read-modify-write of the lease file.

    Without it two promotions racing at once both read epoch N, both write N+1 and
    one silently disappears — the concurrent-promotions test asserts a single winner.
    A lock that cannot be taken (read-only dir, no support) degrades to the old
    unlocked behaviour rather than failing the operation.
    """
    depth = getattr(_lock_state, "depth", 0)
    if depth:
        _lock_state.depth = depth + 1
        try:
            yield
        finally:
            _lock_state.depth -= 1
        return
    with _PROCESS_LOCK:
        lock_path = path.with_name(path.name + ".lock")
        handle = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(lock_path, "a+b")  # noqa: SIM115 — held for the critical section
            if sys.platform == "win32":
                import msvcrt  # noqa: PLC0415

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            logger.warning("brain lease lock unavailable, continuing unlocked: %s", exc)
            if handle is not None:
                handle.close()
            handle = None
        _lock_state.depth = 1
        try:
            yield
        finally:
            _lock_state.depth = 0
            if handle is not None:
                try:
                    if sys.platform == "win32":
                        import msvcrt  # noqa: PLC0415

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl  # noqa: PLC0415

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass  # closing the handle releases it anyway
                handle.close()


class LeaseStore:
    """Read/modify the lease. Every mutating call is atomic and locked."""

    def __init__(self, path: Path | None = None, *, clock=None) -> None:
        self._explicit = Path(path) if path is not None else None
        self._clock = clock or time.time

    # ── locations/io ─────────────────────────────────────────────────────
    @property
    def path(self) -> Path:
        return self._explicit if self._explicit is not None else lease_path()

    def _now(self) -> float:
        return float(self._clock())

    def read(self) -> BrainLease | None:
        """The current lease, or ``None`` when there is none.

        Reads through ``read_text_retrying`` (the WRITE side replaces the file, so a
        reader can catch a transient Windows sharing violation). A corrupt record is
        NOT silently treated as "free": that would hand ownership to whoever asked
        next, which is the bug. It raises, and the caller decides.
        """
        path = self.path
        from navig.core.yaml_io import read_text_retrying  # noqa: PLC0415

        # Check existence FIRST: `read_text_retrying` deliberately retries a missing
        # file (a replace window looks the same), so calling it on a fresh install
        # would log three warnings and delay every first boot.
        if not path.exists():
            return None
        try:
            text = read_text_retrying(path)
        except FileNotFoundError:
            return None
        except OSError:
            raise
        if not text.strip():
            return None
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise BrainOwnershipError(
                f"the Brain lease at {path} is unreadable ({exc}). Refusing to guess "
                "ownership — inspect it, or remove it and re-claim with "
                "`navig brain claim --reason <why>`."
            ) from exc
        if not isinstance(data, dict) or not data.get("owner_id"):
            raise BrainOwnershipError(
                f"the Brain lease at {path} is not a lease record. Refusing to guess "
                "ownership — remove it and re-claim with `navig brain claim`."
            )
        return BrainLease.from_dict(data)

    def _write(self, lease: BrainLease) -> BrainLease:
        from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(lease.to_dict(), indent=2))
        return lease

    # ── status ───────────────────────────────────────────────────────────
    def status(self, *, oid: str | None = None) -> dict[str, Any]:
        """The full ownership picture, JSON-ready (CLI ``--json`` / deck API)."""
        return status_from_lease(
            self.read(),
            oid=oid or default_owner_id(),
            settings=brain_settings(),
            path=str(self.path),
            now=self._now(),
        )

    # ── mutations ────────────────────────────────────────────────────────
    def acquire(
        self,
        *,
        oid: str | None = None,
        role: str | None = None,
        ttl: int | None = None,
        reason: str = "",
        allow_takeover: bool = True,
    ) -> BrainLease:
        """Take the lease if it is free, else join the one we already hold.

        Raises :class:`OwnershipConflict` when a DIFFERENT owner holds a live lease.
        An *expired* lease is taken over only when ``brain.auto_promote`` is enabled
        (or the caller explicitly allows it and the operator asked for a promotion);
        the default is manual, so a paused holder is never silently displaced.
        """
        me = oid or default_owner_id()
        settings = brain_settings()
        want_role = normalize_role(role or settings["role"])
        want_ttl = int(ttl or settings["ttl"])
        now = self._now()

        with _lease_lock(self.path):
            current = self.read()
            if current is None:
                return self._write(
                    BrainLease(
                        owner_id=me,
                        role=want_role,
                        epoch=1,
                        acquired_at=now,
                        heartbeat_at=now,
                        ttl=want_ttl,
                        hostname=hostname(),
                        reason=reason or "first claim",
                    )
                )
            if current.is_free():
                return self._write(
                    replace(
                        current,
                        owner_id=me,
                        role=want_role,
                        epoch=current.epoch + 1,
                        acquired_at=now,
                        heartbeat_at=now,
                        ttl=want_ttl,
                        hostname=hostname(),
                        reason=reason or "claimed a released lease",
                        released_at=None,
                    )
                )
            if current.held_by(me):
                # Already ours — renew. Keeping the epoch stable matters: in-flight
                # writers of THIS process must stay valid across a restart of one
                # service that re-acquires the same lease.
                return self._write(
                    replace(
                        current,
                        heartbeat_at=now,
                        ttl=want_ttl,
                        role=want_role,
                        hostname=hostname(),
                        reason=reason or current.reason,
                    )
                )
            if current.is_live(now):
                raise OwnershipConflict(
                    f"The Brain is owned by {current.hostname or 'another machine'} "
                    f"({current.owner_id}), epoch {current.epoch}, and it is heartbeating "
                    f"({current.expires_in(now)}s of TTL left). Refusing to take it — two "
                    "Brains on one deployment is exactly the bug this lease prevents. "
                    "If that machine is gone: navig brain takeover "
                    f"--from {current.owner_id} --reason <why>"
                )
            # Expired, held by another machine.
            if settings["auto_promote"] and allow_takeover:
                return self._takeover_locked(
                    current,
                    me=me,
                    role=want_role,
                    ttl=want_ttl,
                    reason=reason
                    or f"auto-promoted: lease expired {int(now - current.heartbeat_at)}s ago",
                    now=now,
                )
            raise OwnershipConflict(
                f"The Brain lease held by {current.hostname or 'another machine'} "
                f"({current.owner_id}) has EXPIRED (last heartbeat "
                f"{int(now - current.heartbeat_at)}s ago). Automatic promotion is "
                "disabled (brain.auto_promote=false), so this cannot take it silently. "
                "Takes one deliberate step — navig brain takeover "
                f"--from {current.owner_id} --reason <why>"
            )

    def renew(
        self, *, oid: str | None = None, epoch: int | None = None, ttl: int | None = None
    ) -> BrainLease:
        """Heartbeat. Presents the epoch — a stale writer is refused, not refreshed."""
        me = oid or default_owner_id()
        with _lease_lock(self.path):
            current = self.read()
            if current is None:
                raise BrainOwnershipError(
                    f"No Brain lease exists at {self.path} — nothing to renew. Claim it: "
                    "navig brain claim"
                )
            if epoch is not None and int(epoch) != current.epoch:
                # The fence is checked FIRST: a process whose epoch is stale lost
                # ownership, so "you are not the owner" would be the less actionable
                # half of the same fact.
                raise StaleEpoch(
                    f"Stale epoch {epoch}: the lease is now epoch {current.epoch}. This "
                    "process lost ownership and must stop writing."
                )
            if not current.held_by(me):
                raise OwnershipConflict(
                    f"This machine ({me}) does not hold the Brain lease — "
                    f"{current.hostname or 'another machine'} ({current.owner_id}) does."
                )
            now = self._now()
            return self._write(
                replace(current, heartbeat_at=now, ttl=int(ttl or current.ttl))
            )

    def release(
        self, *, oid: str | None = None, epoch: int | None = None, reason: str = ""
    ) -> BrainLease:
        """Give the lease up cleanly. The epoch is kept so stale writers stay fenced."""
        me = oid or default_owner_id()
        with _lease_lock(self.path):
            current = self.read()
            if current is None:
                raise BrainOwnershipError("No Brain lease exists — nothing to release.")
            if not current.held_by(me):
                raise OwnershipConflict(
                    f"Refusing to release a lease this machine ({me}) does not hold — "
                    f"it belongs to {current.owner_id}."
                )
            if epoch is not None and int(epoch) != current.epoch:
                raise StaleEpoch(
                    f"Stale epoch {epoch} (current {current.epoch}) — this process no "
                    "longer owns the lease."
                )
            now = self._now()
            return self._write(
                replace(current, released_at=now, reason=reason or "released")
            )

    def takeover(
        self,
        *,
        oid: str | None = None,
        role: str | None = None,
        ttl: int | None = None,
        reason: str,
        from_owner: str | None = None,
        force: bool = False,
    ) -> BrainLease:
        """Explicitly displace the current holder, bump the epoch, audit it.

        Validation, in order:

        * ``reason`` is required — a takeover with no stated cause is not auditable;
        * a LIVE holder needs ``force=True`` AND ``from_owner`` equal to the holder you
          intended to displace. If the holder changed while you were looking, the
          takeover fails instead of displacing the wrong Brain (a lost race, safely);
        * an EXPIRED holder needs no ``force`` — expiry is the evidence.
        """
        if not str(reason or "").strip():
            raise BrainOwnershipError(
                "A takeover must state why: pass --reason <why>. It is written to the "
                "lease audit trail and the migration receipt."
            )
        me = oid or default_owner_id()
        settings = brain_settings()
        want_role = normalize_role(role or settings["role"])
        want_ttl = int(ttl or settings["ttl"])
        now = self._now()

        with _lease_lock(self.path):
            current = self.read()
            if current is None:
                return self._write(
                    BrainLease(
                        owner_id=me,
                        role=want_role,
                        epoch=1,
                        acquired_at=now,
                        heartbeat_at=now,
                        ttl=want_ttl,
                        hostname=hostname(),
                        reason=f"takeover: {reason}",
                    )
                )
            if current.held_by(me) and not current.is_free():
                return self._write(
                    replace(current, heartbeat_at=now, ttl=want_ttl)
                )
            if current.is_live(now):
                if not force:
                    raise OwnershipConflict(
                        f"{current.hostname or 'another machine'} ({current.owner_id}) holds "
                        f"the Brain lease and is heartbeating ({current.expires_in(now)}s of "
                        "TTL left). Stop it first, or pass --force --from "
                        f"{current.owner_id} if you are deliberately moving the Brain."
                    )
                if from_owner != current.owner_id:
                    raise OwnershipConflict(
                        "Refusing a forced takeover without matching the current holder: "
                        f"expected --from {current.owner_id} "
                        f"(got {from_owner or 'nothing'}). The lease may have moved since "
                        "you looked — re-run `navig brain status` and decide again."
                    )
            elif from_owner and from_owner != current.owner_id:
                raise OwnershipConflict(
                    f"The expired lease belongs to {current.owner_id}, not {from_owner}. "
                    "Re-run `navig brain status` and decide again."
                )
            return self._takeover_locked(
                current,
                me=me,
                role=want_role,
                ttl=want_ttl,
                reason=reason,
                now=now,
                forced=current.is_live(now),
            )

    def _takeover_locked(
        self,
        current: BrainLease,
        *,
        me: str,
        role: str,
        ttl: int,
        reason: str,
        now: float,
        forced: bool = False,
    ) -> BrainLease:
        audit = {
            "at": now,
            "from_owner": current.owner_id,
            "from_hostname": current.hostname,
            "to_owner": me,
            "to_hostname": hostname(),
            "from_epoch": current.epoch,
            "to_epoch": current.epoch + 1,
            "reason": reason,
            "forced": bool(forced),
            "expired": bool(current.is_expired(now)),
        }
        lease = replace(
            current,
            owner_id=me,
            role=role,
            epoch=current.epoch + 1,
            acquired_at=now,
            heartbeat_at=now,
            ttl=ttl,
            hostname=hostname(),
            reason=reason,
            released_at=None,
            takeovers=(*current.takeovers, audit)[-50:],
        )
        logger.warning(
            "Brain ownership taken over: %s -> %s (epoch %d -> %d): %s",
            current.owner_id,
            me,
            current.epoch,
            lease.epoch,
            reason,
        )
        return self._write(lease)

    # ── fencing ──────────────────────────────────────────────────────────
    def current_epoch(self) -> int:
        lease = self.read()
        return lease.epoch if lease is not None else 0

    def assert_epoch(self, epoch: int | None, *, who: str = "writer") -> None:
        """Refuse a write that presents an epoch the lease has moved past."""
        if epoch is None:
            return
        lease = self.read()
        actual = lease.epoch if lease is not None else 0
        if int(epoch) != actual:
            raise StaleEpoch(
                f"Refusing a write from {who}: it presents Brain epoch {epoch}, but the "
                f"lease is at epoch {actual}. This process lost ownership (a takeover "
                "happened) and must stop writing."
            )

    def fenced_write(
        self, path: Path, text: str, *, epoch: int | None, who: str = "writer"
    ) -> Path:
        """Atomic write gated on the fence: the ONE way a singleton writes state."""
        self.assert_epoch(epoch, who=who)
        from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, text)
        return path
