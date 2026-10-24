"""The Brain ownership AUTHORITY — a shared, authenticated owner record over HTTP.

Why this module exists
----------------------
The lease in :mod:`navig.brain.lease` is a JSON file inside one machine's config
dir. That is a good local safety net and it is **not** an authority: two machines
of one deployment (same bot token, same ``deck.api_key``, same schedules) each
read their own file, so neither can see the other. The incident — both machines
serving Telegram, a Windows gateway re-setting the webhook on every boot — is
exactly what two private files cannot prevent.

The authority is the Lighthouse Durable Object (`services/lighthouse`,
``OwnershipRecord``): one object per tenant, where the tenant is
``sha256(deck.api_key)`` — the identity the deployment already has. There is no
second secret and no fleet master key. A Durable Object handles one request at a
time, so the epoch compare-and-set is real across machines regardless of clocks,
filesystems or reachability between the machines themselves.

What lives where
----------------
``AuthorityClient``        the HTTP calls: acquire / renew / release / takeover /
                           mark_moved / clear_moved / status, with timeouts,
                           bounded retries and error mapping.
``AuthorityLeaseStore``    the same surface ``LeaseStore`` exposes, so the guards,
                           the CLI and the deck API are unchanged in shape. It is
                           what ``guard`` uses whenever an authority is configured.
``<config>/brain/authority-cache.json``
                           a **read-only mirror** of the last authoritative answer.
                           It is never the source of truth: it is shown when the
                           authority is unreachable and it never promotes anybody.

Failure semantics (deliberate, and the whole point)
---------------------------------------------------
An authority that is configured but unreachable makes a singleton **fail closed**
(:data:`navig.brain.lease.EXIT_OWNERSHIP_REFUSED`, 77). A partition is not a
licence to promote locally: "nobody answered" must never become "I am the Brain".
Manual promotion stays the default; an expired record is taken over automatically
only when ``brain.auto_promote`` is on, and the authority records the takeover.

A deployment with **no** authority configured (a single-machine install with no
Lighthouse edge) still works: the local lease is then the only record, and status
says ``authority.mode = local`` so nobody mistakes it for the shared guarantee.
Set ``brain.authority_required: true`` to refuse even that.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx

from navig.brain.identity import hostname
from navig.brain.identity import owner_id as default_owner_id
from navig.brain.lease import (
    EXIT_OWNERSHIP_REFUSED,
    SINGLETON_ROLES,
    BrainLease,
    BrainOwnershipError,
    OwnershipConflict,
    StaleEpoch,
    brain_settings,
    normalize_role,
    status_from_lease,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AUTHORITY_PATH",
    "AuthorityClient",
    "AuthorityError",
    "AuthorityLeaseStore",
    "AuthorityRefused",
    "AuthorityUnavailable",
    "MovedBrain",
    "authority_acquire",
    "authority_cache_path",
    "authority_mark_moved",
    "authority_release",
    "authority_renew",
    "authority_status",
    "authority_takeover",
    "base_url",
    "cached_lease",
    "clear_cache",
    "client_or_none",
    "configured",
    "default_client",
    "lease_store",
    "override",
    "read_cache",
    "required",
    "write_cache",
]

#: The Worker route (`services/lighthouse/src/index.ts`) that reaches the
#: OwnershipRecord Durable Object. Bearer deck key derives the tenant.
AUTHORITY_PATH = "/api/ownership"

_CACHE_FILE = "authority-cache.json"

#: How long a fetched epoch may answer a fence check before it is re-read. Short
#: on purpose: this is the window in which a takeover is not yet observed by a
#: writer on the machine that lost ownership.
DEFAULT_CACHE_TTL = 10.0

#: Backoff between transport retries (seconds). Small: a boot must not hang on a
#: flapping edge, and the answer after the retries is a refusal, not a wait.
_BACKOFF = (0.15, 0.35)


# ── errors ───────────────────────────────────────────────────────────────────


class AuthorityError(BrainOwnershipError):
    """Base class for "the authoritative record could not be consulted"."""

    exit_code = EXIT_OWNERSHIP_REFUSED


class AuthorityUnavailable(AuthorityError):
    """No authoritative answer (unreachable, timed out, or 5xx).

    Carries exit code 77 because the only safe response at a singleton start is
    to refuse. Nothing may fall back to the local file as an authority.
    """


class AuthorityRefused(AuthorityError):
    """The authority answered and said no: bad key, wrong role, or a refusal."""


class MovedBrain(OwnershipConflict):
    """The authority's record says this Brain moved away — do not re-claim it."""


# ── where the authority is ───────────────────────────────────────────────────


def base_url() -> str:
    """The edge base URL, or ``""`` when this install has no shared authority.

    ``brain.authority_url`` wins, then ``cloud.lighthouse_url`` (the URL the deck,
    the Telegram webhook and the uplink already use). The environment overrides
    both, which is how tests and a supervised service pin it.
    """
    try:
        return str(brain_settings().get("authority_url") or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001 — a settings read must never break a guard
        return ""


def _deck_api_key() -> str:
    """This install's ``deck.api_key`` — the Lighthouse tenant identity."""
    from navig.brain import capabilities  # noqa: PLC0415 — avoids an import cycle

    try:
        return str(capabilities.config_section("deck").get("api_key") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _api_key() -> str:
    return (os.environ.get("NAVIG_BRAIN_AUTHORITY_KEY") or _deck_api_key()).strip()


def required() -> bool:
    """Has the operator demanded a shared authority even for a single machine?"""
    try:
        return bool(brain_settings().get("authority_required"))
    except Exception:  # noqa: BLE001
        return False


# ── cache: a mirror, never the authority ─────────────────────────────────────


def authority_cache_path() -> Path:
    from navig.platform import paths  # noqa: PLC0415

    return paths.config_dir() / "brain" / _CACHE_FILE


def write_cache(lease: BrainLease | None, *, url: str) -> None:
    """Mirror the last authoritative answer. Best-effort; never raises."""
    try:
        from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

        path = authority_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "fetched_at": time.time(),
            "url": url,
            "record": lease.to_dict() if lease is not None else None,
        }
        atomic_write_text(path, json.dumps(payload, indent=2))
    except Exception as exc:  # noqa: BLE001 — a cache write is never load-bearing
        logger.debug("brain authority cache not written: %s", exc)


def read_cache() -> dict[str, Any] | None:
    """The mirrored answer, with ``stale`` computed. ``None`` when there is none."""
    from navig.core.yaml_io import read_text_retrying  # noqa: PLC0415

    path = authority_cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(read_text_retrying(path))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    fetched = float(data.get("fetched_at") or 0.0)
    ttl = float(brain_settings().get("authority_cache_ttl") or DEFAULT_CACHE_TTL)
    return {
        "record": data.get("record") if isinstance(data.get("record"), dict) else None,
        "fetched_at": fetched,
        "age": max(0.0, time.time() - fetched) if fetched else None,
        "stale": not fetched or (time.time() - fetched) > ttl,
        "url": str(data.get("url") or ""),
    }


def cached_lease() -> BrainLease | None:
    entry = read_cache() or {}
    record = entry.get("record")
    if not isinstance(record, dict) or not record.get("owner_id"):
        return None
    try:
        return BrainLease.from_dict(record)
    except (TypeError, ValueError):
        return None


def clear_cache() -> None:
    try:
        authority_cache_path().unlink(missing_ok=True)
    except OSError:
        pass


# ── the HTTP client ──────────────────────────────────────────────────────────

_TRANSPORT_ERRORS = (httpx.TimeoutException, httpx.TransportError)


def _refusal(response: httpx.Response) -> BrainOwnershipError:
    """Map the authority's refusal onto the type the rest of NAVIG already uses."""
    body: dict[str, Any] = {}
    try:
        parsed = response.json()
        body = parsed if isinstance(parsed, dict) else {}
    except ValueError:
        body = {}
    code = str(body.get("code") or "").strip()
    error = str(body.get("error") or "").strip()
    if response.status_code == 401:
        hint = (
            "The deck.api_key is the Lighthouse tenant — check it is the one this edge knows."
        )
        return AuthorityRefused(
            f"the Brain authority rejected this install's credentials (HTTP 401). "
            f"{error or hint}"
        )
    if code in ("stale_epoch",):
        return StaleEpoch(
            error or f"stale epoch: the authority is at epoch {body.get('epoch')}. This process "
            "lost ownership and must stop writing."
        )
    if code == "moved":
        return MovedBrain(error or "the authority's record says this Brain moved away.")
    if code in ("role_forbidden",):
        return AuthorityRefused(error or "this install's role may not change Brain ownership.")
    if code in ("reason_required", "owner_id_required", "moved_to_required", "unknown_op",
                "invalid_json"):
        return AuthorityError(error or f"the authority refused the request ({code}).")
    if code in ("held", "expired", "not_owner", "live_requires_force", "wrong_from", "released"):
        return OwnershipConflict(error or "the authority refused the ownership change.")
    return AuthorityRefused(
        error or f"the Brain authority refused the request (HTTP {response.status_code})."
    )


class AuthorityClient:
    """HTTP client for one deployment's owner record.

    ``transport`` is injected in tests (``httpx.MockTransport``); production uses a
    normal pooled client. Every mutation carries a ``request_id`` so a retry after
    a timeout cannot apply twice — the authority replays its stored answer.
    """

    def __init__(
        self,
        base: str,
        api_key: str,
        *,
        timeout: float = 3.0,
        retries: int = 1,
        transport: httpx.BaseTransport | None = None,
        clock=time.time,
    ) -> None:
        self.base = str(base or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.timeout = max(0.5, float(timeout))
        self.retries = max(0, int(retries))
        self._clock = clock
        self._transport = transport
        self._http: httpx.Client | None = None

    # ── plumbing ─────────────────────────────────────────────────────────
    @property
    def url(self) -> str:
        return f"{self.base}{AUTHORITY_PATH}"

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                timeout=httpx.Timeout(self.timeout, connect=min(2.0, self.timeout)),
                transport=self._transport,
                headers={"accept": "application/json"},
            )
        return self._http

    def close(self) -> None:
        if self._http is not None:
            try:
                self._http.close()
            finally:
                self._http = None

    def __enter__(self) -> AuthorityClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _post(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"op": op, **payload}
        if op != "status":
            body.setdefault("request_id", uuid.uuid4().hex)
        body.setdefault("hostname", hostname())
        body.setdefault("owner_id", default_owner_id())
        headers = {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        last: Exception | None = None
        attempts = self.retries + 1
        for attempt in range(attempts):
            try:
                response = self._client().post(self.url, json=body, headers=headers)
            except _TRANSPORT_ERRORS as exc:
                last = exc
            except httpx.HTTPError as exc:  # a malformed URL, a broken transport
                raise AuthorityUnavailable(
                    f"the Brain authority at {self.url} could not be called: {exc}"
                ) from exc
            else:
                if response.status_code >= 500:
                    last = AuthorityUnavailable(
                        f"the Brain authority at {self.url} failed with HTTP "
                        f"{response.status_code}"
                    )
                elif response.status_code == 200:
                    try:
                        parsed = response.json()
                    except ValueError as exc:
                        raise AuthorityUnavailable(
                            f"the Brain authority at {self.url} returned non-JSON "
                            f"(HTTP {response.status_code})"
                        ) from exc
                    if not isinstance(parsed, dict) or parsed.get("ok") is False:
                        raise _refusal(response)
                    return parsed
                else:
                    raise _refusal(response)
            if attempt < attempts - 1:
                time.sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])
        raise AuthorityUnavailable(
            f"the Brain authority at {self.url} did not answer after {attempts} attempt(s) "
            f"({type(last).__name__ if last else 'no response'}: {last}). Refusing to fall "
            "back to this machine's local file — that file is a cache, not the authority. "
            "Check the Lighthouse edge (navig lighthouse status) and the network, then retry."
        )

    # ── reads ────────────────────────────────────────────────────────────
    def status(self, *, oid: str | None = None, role: str | None = None) -> dict[str, Any]:
        """The authority's status document. Read-only; safe for a client install."""
        payload = self._post(
            "status",
            {"owner_id": oid or default_owner_id(), "role": normalize_role(role or brain_settings()["role"])},
        )
        record = payload.get("record")
        lease = BrainLease.from_dict(record) if isinstance(record, dict) and record.get("owner_id") else None
        if lease is not None:
            write_cache(lease, url=self.url)
        return payload

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
        settings = brain_settings()
        payload = self._post(
            "acquire",
            {
                "owner_id": oid or default_owner_id(),
                "role": normalize_role(role or settings["role"]),
                "ttl": int(ttl or settings["ttl"]),
                "reason": reason,
                # The SERVER decides whether an expired record may be promoted;
                # the client only reports the operator's setting.
                "auto_promote": bool(settings["auto_promote"] and allow_takeover),
            },
        )
        return self._lease(payload)

    def renew(self, *, oid: str | None = None, epoch: int | None = None, ttl: int | None = None) -> BrainLease:
        settings = brain_settings()
        return self._lease(
            self._post(
                "renew",
                {
                    "owner_id": oid or default_owner_id(),
                    "role": normalize_role(settings["role"]),
                    "epoch": epoch,
                    "ttl": int(ttl or settings["ttl"]),
                },
            )
        )

    def release(self, *, oid: str | None = None, epoch: int | None = None, reason: str = "") -> BrainLease:
        return self._lease(
            self._post(
                "release",
                {
                    "owner_id": oid or default_owner_id(),
                    "role": normalize_role(brain_settings()["role"]),
                    "epoch": epoch,
                    "reason": reason,
                },
            )
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
        settings = brain_settings()
        return self._lease(
            self._post(
                "takeover",
                {
                    "owner_id": oid or default_owner_id(),
                    "role": normalize_role(role or settings["role"]),
                    "ttl": int(ttl or settings["ttl"]),
                    "reason": reason,
                    "from_owner": from_owner or "",
                    "force": bool(force),
                },
            )
        )

    def mark_moved(
        self,
        *,
        oid: str | None = None,
        to: str,
        migration: str = "",
        reason: str,
        epoch: int | None = None,
    ) -> BrainLease:
        """Record the move IN the authority, so the source can never re-claim."""
        return self._lease(
            self._post(
                "mark_moved",
                {
                    "owner_id": oid or default_owner_id(),
                    "role": normalize_role(brain_settings()["role"]),
                    "epoch": epoch,
                    "reason": reason,
                    "moved_marker": {"to": to, "migration": migration},
                },
            )
        )

    def clear_moved(
        self, *, oid: str | None = None, reason: str = "", epoch: int | None = None
    ) -> BrainLease:
        return self._lease(
            self._post(
                "clear_moved",
                {
                    "owner_id": oid or default_owner_id(),
                    "role": normalize_role(brain_settings()["role"]),
                    "epoch": epoch,
                    "reason": reason,
                },
            )
        )

    # ── result shaping ───────────────────────────────────────────────────
    def _lease(self, payload: dict[str, Any]) -> BrainLease:
        record = payload.get("record")
        if not isinstance(record, dict) or not record.get("owner_id"):
            raise AuthorityUnavailable(
                "the Brain authority returned no owner record for a mutation that "
                "reported success — refusing to guess ownership"
            )
        lease = BrainLease.from_dict(record)
        write_cache(lease, url=self.url)
        return lease


# ── one client per process (and the test override) ───────────────────────────

_DEFAULT: AuthorityClient | None = None
_DEFAULT_KEY: tuple[str, str, float, int] | None = None
_OVERRIDE: list[AuthorityClient] = []


def default_client() -> AuthorityClient | None:
    """The configured client, or ``None`` when this install has no authority."""
    global _DEFAULT, _DEFAULT_KEY
    url = base_url()
    key = _api_key()
    if not url or not key:
        return None
    settings = brain_settings()
    signature = (url, key, float(settings["authority_timeout"]), int(settings["authority_retries"]))
    if _DEFAULT is None or _DEFAULT_KEY != signature:
        _DEFAULT = AuthorityClient(
            url,
            key,
            timeout=float(settings["authority_timeout"]),
            retries=int(settings["authority_retries"]),
        )
        _DEFAULT_KEY = signature
    return _DEFAULT


def client_or_none() -> AuthorityClient | None:
    """The client in effect: a test override first, then the configured one."""
    if _OVERRIDE:
        return _OVERRIDE[-1]
    return default_client()


def configured() -> bool:
    return client_or_none() is not None


@contextmanager
def override(client: AuthorityClient | None) -> Iterator[AuthorityClient | None]:
    """Use *client* for the duration of the block (tests / an explicit CLI target).

    Nested overrides unwind correctly; ``None`` means "pretend nothing is
    configured", which is how a test pins the single-machine (local) path.
    """
    _OVERRIDE.append(client)  # type: ignore[arg-type]
    try:
        yield client
    finally:
        _OVERRIDE.pop()


def lease_store(store: Any = None, *, client: AuthorityClient | None = None) -> Any:
    """The record store the guards should use.

    Remote when an authority is configured (the local file then becomes a cache),
    otherwise the local :class:`~navig.brain.lease.LeaseStore` — the documented
    single-machine mode. An explicit *store* only applies in local mode, so a
    caller can never accidentally substitute a private file for the authority.
    """
    active = client if client is not None else client_or_none()
    if active is not None:
        return AuthorityLeaseStore(active)
    if store is not None:
        return store
    from navig.brain.lease import LeaseStore  # noqa: PLC0415

    return LeaseStore()


# ── the store adapter guard / CLI / deck already know ────────────────────────


class AuthorityLeaseStore:
    """``LeaseStore``'s surface, backed by the authority instead of a file.

    The local lease file is written only as a cache mirror. It is never read to
    decide anything while this class is in use: a read-only cache that is silently
    trusted is how a stale machine talks itself back into being the Brain.
    """

    def __init__(self, client: AuthorityClient, *, clock=time.time) -> None:
        self.client = client
        self._clock = clock
        self._epoch: tuple[int, float] | None = None

    # ── locations ────────────────────────────────────────────────────────
    @property
    def path(self) -> Path:
        return authority_cache_path()

    @property
    def url(self) -> str:
        return self.client.url

    # ── reads ────────────────────────────────────────────────────────────
    def read(self) -> BrainLease | None:
        """The authoritative record right now (raising when it cannot be read)."""
        payload = self.client.status(oid=default_owner_id())
        return self._record(payload)

    def _record(self, payload: dict[str, Any]) -> BrainLease | None:
        record = payload.get("record")
        if isinstance(record, dict) and record.get("owner_id"):
            return BrainLease.from_dict(record)
        return None

    def status(self, *, oid: str | None = None) -> dict[str, Any]:
        """The shared status document, with an explicit ``authority`` block.

        Unreachable is reported, not invented: the state comes from the mirror and
        says so (``reachable: false``, ``cached_at``), because a status view that
        cannot say "I could not ask" is the green-light-over-an-unknown failure.
        """
        me = oid or default_owner_id()
        settings = brain_settings()
        try:
            payload = self.client.status(oid=me)
        except AuthorityUnavailable as exc:
            entry = read_cache() or {}
            mirror = None
            if isinstance(entry.get("record"), dict):
                try:
                    mirror = BrainLease.from_dict(entry["record"])
                except (TypeError, ValueError):
                    mirror = None
            block = {
                "mode": "remote",
                "url": self.url,
                "reachable": False,
                "error": str(exc),
                "cached_at": entry.get("fetched_at"),
                # Unreachable means unconfirmable: whatever the mirror says, it is
                # not a current answer. Never report it as fresh.
                "stale": True,
            }
            doc = status_from_lease(
                mirror,
                oid=me,
                settings=settings,
                path=str(self.path),
                now=self._clock(),
                authority=block,
            )
            if mirror is not None:
                doc["state"] = f"cached-{doc['state']}"
                doc["summary"] = (
                    f"{doc['summary']} — CACHED: the authority is unreachable, so this is "
                    "the last answer, not a current one"
                )
            else:
                doc["state"] = "unknown"
                doc["summary"] = "unknown — the authority is unreachable and nothing is cached"
            doc["can_claim"] = False
            doc["can_takeover"] = False
            return doc
        server = payload.get("status") if isinstance(payload.get("status"), dict) else {}
        lease = self._record(payload)
        block = {
            "mode": "remote",
            "url": self.url,
            "reachable": True,
            "epoch": server.get("epoch", lease.epoch if lease else 0),
            "server_time": payload.get("server_time"),
        }
        doc = status_from_lease(
            lease, oid=me, settings=settings, path=str(self.path), now=self._clock(), authority=block
        )
        # The server's view is the authoritative one for these two facts.
        if server:
            doc["can_claim"] = bool(server.get("can_claim")) and settings["role"] in SINGLETON_ROLES
            doc["can_takeover"] = (
                bool(server.get("can_takeover")) and settings["role"] in SINGLETON_ROLES
            )
            doc["you"]["is_owner"] = bool(server.get("is_owner"))
            if server.get("summary"):
                doc["summary"] = str(server["summary"])
        return doc

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
        lease = self.client.acquire(
            oid=oid, role=role, ttl=ttl, reason=reason, allow_takeover=allow_takeover
        )
        self._epoch = (lease.epoch, self._clock())
        return lease

    def renew(self, *, oid: str | None = None, epoch: int | None = None, ttl: int | None = None) -> BrainLease:
        lease = self.client.renew(oid=oid, epoch=epoch, ttl=ttl)
        self._epoch = (lease.epoch, self._clock())
        return lease

    def release(self, *, oid: str | None = None, epoch: int | None = None, reason: str = "") -> BrainLease:
        lease = self.client.release(oid=oid, epoch=epoch, reason=reason)
        self._epoch = None
        return lease

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
        lease = self.client.takeover(
            oid=oid, role=role, ttl=ttl, reason=reason, from_owner=from_owner, force=force
        )
        self._epoch = (lease.epoch, self._clock())
        return lease

    # ── fencing ──────────────────────────────────────────────────────────
    def current_epoch(self) -> int:
        return self._epoch_value()

    def _epoch_value(self) -> int:
        if self._epoch is not None:
            epoch, at = self._epoch
            ttl = float(brain_settings().get("authority_cache_ttl") or DEFAULT_CACHE_TTL)
            if self._clock() - at <= ttl:
                return int(epoch)
        payload = self.client.status(oid=default_owner_id())
        lease = self._record(payload)
        epoch = lease.epoch if lease is not None else 0
        self._epoch = (epoch, self._clock())
        return epoch

    def assert_epoch(self, epoch: int | None, *, who: str = "writer") -> None:
        """Refuse a write that presents an epoch the authority has moved past.

        Cached for at most ``brain.authority_cache_ttl`` seconds (default 10). When
        the authority cannot be consulted the write is REFUSED
        (:class:`AuthorityUnavailable`) — a partition is not permission.
        """
        if epoch is None:
            return
        actual = self._epoch_value()
        if int(epoch) != actual:
            raise StaleEpoch(
                f"Refusing a write from {who}: it presents Brain epoch {epoch}, but the "
                f"authority is at epoch {actual}. This process lost ownership (a takeover "
                "happened) and must stop writing."
            )

    def fenced_write(
        self, path: Path, text: str, *, epoch: int | None, who: str = "writer"
    ) -> Path:
        self.assert_epoch(epoch, who=who)
        from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, text)
        return target


# ── module-level verbs (the documented entry points) ─────────────────────────


def _client(client: AuthorityClient | None) -> AuthorityClient:
    active = client if client is not None else client_or_none()
    if active is None:
        raise AuthorityUnavailable(
            "no Brain authority is configured on this install (brain.authority_url / "
            "cloud.lighthouse_url and deck.api_key). Deploy the Lighthouse edge "
            "(`navig lighthouse deploy`), or set brain.authority_url. Single-machine "
            "installs may keep using the local record, but that is not a shared authority."
        )
    return active


def authority_acquire(
    *,
    oid: str | None = None,
    role: str | None = None,
    ttl: int | None = None,
    reason: str = "",
    allow_takeover: bool = True,
    client: AuthorityClient | None = None,
) -> BrainLease:
    return _client(client).acquire(
        oid=oid, role=role, ttl=ttl, reason=reason, allow_takeover=allow_takeover
    )


def authority_renew(
    *, oid: str | None = None, epoch: int | None = None, ttl: int | None = None,
    client: AuthorityClient | None = None,
) -> BrainLease:
    return _client(client).renew(oid=oid, epoch=epoch, ttl=ttl)


def authority_release(
    *, oid: str | None = None, epoch: int | None = None, reason: str = "",
    client: AuthorityClient | None = None,
) -> BrainLease:
    return _client(client).release(oid=oid, epoch=epoch, reason=reason)


def authority_takeover(
    *,
    oid: str | None = None,
    role: str | None = None,
    ttl: int | None = None,
    reason: str,
    from_owner: str | None = None,
    force: bool = False,
    client: AuthorityClient | None = None,
) -> BrainLease:
    return _client(client).takeover(
        oid=oid, role=role, ttl=ttl, reason=reason, from_owner=from_owner, force=force
    )


def authority_status(
    *, oid: str | None = None, role: str | None = None, client: AuthorityClient | None = None
) -> dict[str, Any]:
    return _client(client).status(oid=oid, role=role)


def authority_mark_moved(
    *,
    to: str,
    migration: str = "",
    reason: str,
    epoch: int | None = None,
    oid: str | None = None,
    client: AuthorityClient | None = None,
) -> BrainLease:
    return _client(client).mark_moved(
        oid=oid, to=to, migration=migration, reason=reason, epoch=epoch
    )
