"""System-event → notification bridge: the one path from *events* to *alerts*.

The gateway queue already carries the events that mean something broke —
``host_down``, ``cert_expired``, ``service_down``, ``cron_job_failed`` — but
nothing subscribed to them, so the operator's phone stayed silent while the deck
event history filled up. This subscribes to that set and turns each one into a
``system_alert`` through the existing router.

Everything the router owns stays the router's: quiet hours, the master toggle and
the per-type × channel matrix are read inside ``notify.dispatch`` and are **not**
reimplemented here. No new notification type either — these are ``system_alert``,
the same row the resource monitor uses.

Three gates, in this order, stop a flapping host from crying wolf:

1. **latch** — ``alert_state(host, metric)`` remembers what we last *said*
   (``firing`` / ``cleared``). An alert whose latch is already ``firing`` is
   skipped, and a recovery is only announced when the latch **is** ``firing`` —
   which is what keeps a routine successful cron run from announcing
   "recovered" after every single job. It lives in SQLite, so a daemon that
   bounces while a host is still down does not re-fire the alert it already sent.
2. **cooldown** — ``dedupe.claim`` per ``event:{type}:{host}:{payload hash}``,
   atomic across processes and restarts.
3. **verified delivery** — every configured channel FAILED → ``dedupe.release``
   (and no latch is written), so the next occurrence retries instead of burning
   the cooldown on a message nobody saw.

A handler exception is swallowed and counted: one bad event must never take the
event processor — or the events queued behind it — down with it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from navig.notify.delivery import all_channels_failed

if TYPE_CHECKING:
    from navig.gateway.system_events import SystemEvent, SystemEventQueue

logger = logging.getLogger("navig.notify")

#: Cooldown for one ``(type, host, payload)`` occurrence. The latch already stops
#: repeats of an UNRESOLVED alert, so this is the backstop for the paths where the
#: latch cannot help (a store write that failed, a type with no latch row).
_COOLDOWN_S = 900.0


@dataclass(frozen=True)
class EventSpec:
    """How one gateway event type becomes a notification."""

    #: ``high`` for DOWN / EXPIRED / FAILED, ``normal`` for WARNING / RECOVERY.
    priority: str
    #: True for the all-clear half of a pair (``*_up`` / ``cron_job_complete``).
    recovery: bool
    #: ``alert_state`` bucket. Shared by a pair so the recovery clears the alert.
    family: str
    title: str
    #: One sentence of context; the payload's scalar fields are appended after it.
    lead: str
    #: Payload keys that make good human subjects (they also leave the fact list).
    subject_keys: tuple[str, ...] = ()
    #: Event types allowed to dispatch **while this one is the firing latch** — a
    #: severity escalation inside the same family, not a repeat. Without it the
    #: latch would swallow the escalation: a cert that was announced "expiring in
    #: 10 days" would never announce "expired" (the whole point of the warning).
    #: Empty for every other spec, so a plain re-fire stays suppressed.
    escalates: tuple[str, ...] = ()


#: The whole contract: which events this bridge consumes, and how each maps onto
#: a ``system_alert``. Keys are the wire values of ``gateway.system_events.EventTypes``
#: (that module is deliberately NOT imported here — importing it runs the whole
#: ``navig.gateway`` package, including the server). ``test_system_events_bridge``
#: asserts the two stay in step.
SYSTEM_EVENT_SPECS: dict[str, EventSpec] = {
    "host_down": EventSpec(
        priority="high",
        recovery=False,
        family="host",
        title="Host down",
        lead="The host stopped answering its checks.",
        subject_keys=("host", "hostname"),
    ),
    "host_up": EventSpec(
        priority="normal",
        recovery=True,
        family="host",
        title="Host up",
        lead="The host is answering its checks again.",
        subject_keys=("host", "hostname"),
    ),
    "host_disk_warning": EventSpec(
        priority="normal",
        recovery=False,
        family="disk",
        title="Disk space low",
        lead="Free disk space crossed the warning threshold.",
        subject_keys=("host", "hostname"),
    ),
    # The all-clear half of the disk pair (Gate 1d). Recovery shares the "disk"
    # family with the warning so it clears the same alert_state latch — a latch
    # with no paired clear sticks firing forever, which is the rock this pair fixes.
    "host_disk_ok": EventSpec(
        priority="normal",
        recovery=True,
        family="disk",
        title="Disk space OK",
        lead="Free disk space is back within the healthy range.",
        subject_keys=("host", "hostname"),
    ),
    "host_memory_warning": EventSpec(
        priority="normal",
        recovery=False,
        family="memory",
        title="Memory low",
        lead="Available memory crossed the warning threshold.",
        subject_keys=("host", "hostname"),
    ),
    "host_memory_ok": EventSpec(
        priority="normal",
        recovery=True,
        family="memory",
        title="Memory OK",
        lead="Memory usage is back within the healthy range.",
        subject_keys=("host", "hostname"),
    ),
    "host_cpu_warning": EventSpec(
        priority="normal",
        recovery=False,
        family="cpu",
        title="CPU load high",
        lead="CPU usage crossed the warning threshold.",
        subject_keys=("host", "hostname"),
    ),
    "host_cpu_ok": EventSpec(
        priority="normal",
        recovery=True,
        family="cpu",
        title="CPU load normal",
        lead="CPU usage is back within the healthy range.",
        subject_keys=("host", "hostname"),
    ),
    "cert_expiry_warning": EventSpec(
        priority="normal",
        recovery=False,
        family="cert",
        title="Certificate expiring",
        lead="A certificate is approaching its expiry date.",
        subject_keys=("domain", "host", "name", "cn"),
        # A warning that has crossed into expiry is the escalation this whole
        # family exists for — it must dispatch even though the warning latched
        # the shared "cert" bucket (the reverse direction stays suppressed).
        escalates=("cert_expired",),
    ),
    "cert_expired": EventSpec(
        priority="high",
        recovery=False,
        family="cert",
        title="Certificate expired",
        lead="A certificate is past its expiry date and is no longer trusted.",
        subject_keys=("domain", "host", "name", "cn"),
    ),
    # The all-clear half of the cert pair (site-probe monitor). Shares the "cert"
    # family with both alerts above so one recovery clears whichever is latched.
    "cert_ok": EventSpec(
        priority="normal",
        recovery=True,
        family="cert",
        title="Certificate OK",
        lead="The certificate is valid again — a renewed or replaced one is in place.",
        subject_keys=("domain", "host", "name", "cn"),
    ),
    # Site uptime (the site-probe monitor) — the site pair, one family so the
    # recovery clears the outage latch.
    "site_down": EventSpec(
        priority="high",
        recovery=False,
        family="site",
        title="Site down",
        lead="The site stopped answering its HTTP check.",
        subject_keys=("host", "site", "url", "name"),
    ),
    "site_up": EventSpec(
        priority="normal",
        recovery=True,
        family="site",
        title="Site up",
        lead="The site is answering its HTTP check again.",
        subject_keys=("host", "site", "url", "name"),
    ),
    "service_down": EventSpec(
        priority="high",
        recovery=False,
        family="service",
        title="Service down",
        lead="The service stopped responding.",
        subject_keys=("service", "service_name", "name"),
    ),
    "service_up": EventSpec(
        priority="normal",
        recovery=True,
        family="service",
        title="Service up",
        lead="The service is responding again.",
        subject_keys=("service", "service_name", "name"),
    ),
    "cron_job_failed": EventSpec(
        priority="high",
        recovery=False,
        family="cron",
        title="Cron job failed",
        lead="The scheduled job did not finish successfully.",
        subject_keys=("job_name", "job_id"),
    ),
    "cron_job_complete": EventSpec(
        priority="normal",
        recovery=True,
        family="cron",
        title="Cron job recovered",
        lead="The scheduled job finished successfully after an earlier failure.",
        subject_keys=("job_name", "job_id"),
    ),
}

#: Every event type the bridge subscribes to (one handler per type).
SUBSCRIBED_EVENT_TYPES: tuple[str, ...] = tuple(SYSTEM_EVENT_SPECS)


# ── payload → words ──────────────────────────────────────────────────────────


def _host_of(payload: dict[str, Any]) -> str:
    for key in ("host", "hostname"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _metric_of(spec: EventSpec, payload: dict[str, Any]) -> str:
    """The ``alert_state`` bucket for this occurrence.

    Cron is per JOB: two jobs sharing a bucket would let a healthy ``backup``
    clear a failing ``deploy``. Every other family is per (host, metric), which
    is what the table's primary key is shaped for.
    """
    if spec.family != "cron":
        return spec.family
    job = payload.get("job_id") or payload.get("job_name") or ""
    return f"cron:{job}"


def _subject(spec: EventSpec, payload: dict[str, Any]) -> str:
    for key in spec.subject_keys:
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def _facts(payload: dict[str, Any], skip: set[str]) -> str:
    """Payload fields worth printing: scalars only, no nested blobs."""
    lines = []
    for key, value in payload.items():
        if key in skip or value is None or isinstance(value, (dict, list, tuple, set)):
            continue
        text = str(value).strip()
        if text:
            lines.append(f"{key}: {text}")
    return "\n".join(lines)


def _numeric(payload: dict[str, Any]) -> float | None:
    """The reading behind an event, when it carries one (for ``alert_state.last_value``)."""
    for key in ("value", "usage", "pct", "percent", "used", "free"):
        value = payload.get(key)
        # bool is an int in Python; a True is not a reading.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        return float(value)
    return None


def dedupe_key(event_type: str, payload: dict[str, Any] | None) -> str:
    """``event:{type}:{host}:{payload hash}`` — one cooldown claim per occurrence.

    Keyed on the PAYLOAD so a genuinely new occurrence (different error text, a
    different reading) is not hidden behind an older one, and on the HOST so two
    machines failing at once each get their own alert.
    """
    data = dict(payload or {})
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str, ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"event:{event_type}:{_host_of(data)}:{digest}"


def _render(event_type: str, payload: dict[str, Any]) -> tuple[str, str, str]:
    """``(title, body, priority)``. Unknown types still get a usable generic card."""
    spec = SYSTEM_EVENT_SPECS.get(event_type)
    if spec is None:
        title = event_type.replace("_", " ").capitalize() or "System event"
        return title, _facts(payload, set()), "normal"
    subject = _subject(spec, payload)
    title = f"{spec.title}: {subject}" if subject else spec.title
    facts = _facts(payload, set(spec.subject_keys))
    body = f"{spec.lead}\n\n{facts}" if facts else spec.lead
    return title, body, spec.priority


# ── the bridge ───────────────────────────────────────────────────────────────


class EventNotifyBridge:
    """Turns subscribed system events into ``system_alert`` dispatches.

    One instance per queue. ``handle`` is the subscriber; it is contracted never
    to raise, because the queue's processor loop would log the error and move on
    but a handler failure still marks the event errored and loses the alert.
    """

    def __init__(self, *, cooldown_s: float = _COOLDOWN_S) -> None:
        self.cooldown_s = float(cooldown_s)
        #: monotonic counters — ``stats()`` is the observability surface.
        self.suppressed = 0
        self.dispatched = 0
        self.errors = 0
        #: WITHHELD ALERTS not yet reported inside a message. It rides along with
        #: the next notification that does go out — a reporter that quietly drops
        #: records looks exactly like a system with only a handful of problems.
        #: Only alerts count: a routine successful cron run gated out by the latch
        #: is correct behaviour, not dropped news, and would otherwise inflate
        #: every unrelated alert with "N suppressed".
        self._pending_alerts = 0

    def stats(self) -> dict[str, int]:
        return {
            "suppressed": self.suppressed,
            "pending_alerts": self._pending_alerts,
            "dispatched": self.dispatched,
            "errors": self.errors,
        }

    async def handle(self, event: "SystemEvent") -> None:
        """Queue subscriber entry point. Logs and counts; never raises."""
        try:
            await self._handle(event)
        except Exception:  # noqa: BLE001 — one bad event must not kill the processor
            self.errors += 1
            logger.exception(
                "event→notify bridge failed for %s (id=%s)",
                getattr(event, "event_type", "?"),
                getattr(event, "id", "?"),
            )

    def _suppress(self, spec: EventSpec, event_type: str, why: str) -> None:
        self.suppressed += 1
        if not spec.recovery:
            self._pending_alerts += 1
        logger.debug("event→notify: suppressed %s (%s)", event_type, why)

    async def _handle(self, event: "SystemEvent") -> None:
        from navig.notify import dedupe, dispatch
        from navig.notify.store import load_alert_states, save_alert_state

        event_type = event.event_type
        spec = SYSTEM_EVENT_SPECS.get(event_type)
        if spec is None:
            # Only subscribed types get here; a re-registration drift must not crash.
            return
        payload = event.payload if isinstance(event.payload, dict) else {}
        host = _host_of(payload)
        metric = _metric_of(spec, payload)

        # ── gate 1: what did we last SAY about this? ────────────────────────
        states = await asyncio.to_thread(load_alert_states)
        row = states.get((host, metric)) or {}
        state = row.get("state")
        if spec.recovery:
            if state != "firing":
                self._suppress(spec, event_type, "no alert outstanding")
                return
        elif state == "firing":
            # A repeat of the SAME alert — possibly before a restart, with no
            # all-clear since — is the cries-wolf pattern this latch exists to
            # prevent. The exception is a declared severity escalation: the
            # latched spec may list this event type as one it escalates INTO
            # (cert warning → cert expired), which is new information, not a
            # re-fire, so it falls through and dispatches.
            latched = SYSTEM_EVENT_SPECS.get(str(row.get("event_type") or ""))
            if latched is None or event_type not in latched.escalates:
                self._suppress(spec, event_type, "already firing")
                return

        # ── gate 2: cooldown, cross-process ─────────────────────────────────
        key = dedupe_key(event_type, payload)
        if not await asyncio.to_thread(dedupe.claim, key, self.cooldown_s):
            self._suppress(spec, event_type, "cooldown")
            return

        title, body, priority = _render(event_type, payload)
        pending = self._pending_alerts
        if pending:
            body += f"\n\n(+{pending} further alert(s) suppressed since the last one)"

        data: dict[str, Any] = dict(payload)
        data.update({"event_type": event_type, "host": host, "metric": metric})
        if spec.recovery:
            # The resources-monitor convention: an all-clear is day-bound routine
            # (the chat tidy may record it); an alert never is.
            data["tidy"] = "status"

        outcome = await dispatch(
            "system_alert", title, body, priority=priority, data=data
        )

        # ── gate 3: only a VERIFIED delivery settles the claim ──────────────
        if all_channels_failed(outcome):
            # Nobody was told: hand the cooldown back so the next occurrence
            # retries instead of losing the event twice. No latch is written, so
            # gate 1 will not block that retry either. An INTENTIONAL mute
            # (channels == []) is not a failure and does latch — same rule as the
            # resource monitor: a muted system must not churn.
            await asyncio.to_thread(dedupe.release, key)
            logger.warning(
                "event→notify: %s reached NO channel (every configured one failed)",
                event_type,
            )
            return

        self._pending_alerts = 0
        self.dispatched += 1
        await asyncio.to_thread(
            save_alert_state,
            host,
            metric,
            "cleared" if spec.recovery else "firing",
            last_value=_numeric(payload),
            event_type=event_type,
        )


class EventNotifyBridgeHandle:
    """The unsubscribe callable ``install_event_notify_bridge`` returns.

    Calling it removes every handler and is idempotent; ``.bridge`` exposes the
    counters for tests and diagnostics, ``.installed`` says whether it is live.
    """

    def __init__(
        self,
        queue: "SystemEventQueue",
        handler: Callable[..., Any],
        event_types: tuple[str, ...],
        bridge: EventNotifyBridge,
    ) -> None:
        self._queue = queue
        self._handler = handler
        self._event_types = event_types
        self.bridge = bridge
        self.installed = True

    def __call__(self) -> None:
        if not self.installed:
            return
        self.installed = False
        for event_type in self._event_types:
            self._queue.unsubscribe(event_type, self._handler)
        if _INSTALLED.get(self._queue) is self:
            del _INSTALLED[self._queue]
        logger.info("event→notify bridge uninstalled")


#: One bridge per queue — a second ``install`` on the same queue would dispatch
#: every event twice. Weak keys so a discarded test queue cannot pin this table.
_INSTALLED: "WeakKeyDictionary[SystemEventQueue, EventNotifyBridgeHandle]" = (
    WeakKeyDictionary()
)


def install_event_notify_bridge(
    event_queue: "SystemEventQueue", *, cooldown_s: float = _COOLDOWN_S
) -> EventNotifyBridgeHandle:
    """Subscribe the bridge to every event type in :data:`SYSTEM_EVENT_SPECS`.

    Idempotent per queue: a second call returns the live handle rather than
    stacking a duplicate subscriber. Returns the unsubscribe callable.
    """
    existing = _INSTALLED.get(event_queue)
    if existing is not None and existing.installed:
        return existing

    bridge = EventNotifyBridge(cooldown_s=cooldown_s)
    handler = bridge.handle
    subscribed: list[str] = []
    try:
        for event_type in SUBSCRIBED_EVENT_TYPES:
            event_queue.subscribe(event_type, handler)
            subscribed.append(event_type)
    except BaseException:
        # Never leave a half-subscribed bridge: the types registered so far would
        # keep dispatching with no handle to remove them.
        for event_type in subscribed:
            event_queue.unsubscribe(event_type, handler)
        raise

    handle = EventNotifyBridgeHandle(event_queue, handler, SUBSCRIBED_EVENT_TYPES, bridge)
    _INSTALLED[event_queue] = handle
    logger.info(
        "event→notify bridge installed (%d event types, cooldown %ss)",
        len(SUBSCRIBED_EVENT_TYPES),
        bridge.cooldown_s,
    )
    return handle


# ── for callers that have no queue ───────────────────────────────────────────


async def emit_or_notify(
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    event_queue: "SystemEventQueue | None" = None,
) -> Any:
    """Emit *event_type* through the event queue if there is one, else notify directly.

    The point is that a caller with **no gateway** — a detached cron child, a
    one-shot script — must not emit into the void: ``SystemEventQueue.emit`` on a
    queue nobody drains (or that does not exist) delivers nothing. So:

    * a queue is given (or a :class:`SystemEventQueue` exists in this process) →
      ``event_queue.emit(...)`` and the **event id** comes back; the bridge turns
      it into a notification when installed on that queue. This is the deduped,
      latched path — it only produces a notification once something has called
      ``install_event_notify_bridge`` on that queue.
    * no queue → a direct ``notify.dispatch`` and its **outcome dict** comes back.
      This path is a PLAIN dispatch: no cooldown, no ``alert_state`` latch (it is
      for one-shot callers that have no queue to carry state through), but the
      router's own quiet hours and master toggle still apply.

    **What a detached direct dispatch can actually deliver** (read from
    ``notify/router.py::_send_one``, which the Bot-API path is designed to cover
    out-of-process):

    * ``deck`` — works: it appends to the shared ``notify_feed`` table in
      ``notify.db``, which the daemon reads back. The live SSE pop is skipped
      (no gateway bound), so the entry appears on the next read, not instantly.
    * ``telegram`` — works: with no channel object bound it takes the direct
      Bot-API path (``telegram_routes.deliver``) reading the bot token and chat
      from config. This is the branch the router documents as "no channel object
      exists outside the gateway".
    * ``pushover``, ``email``, ``sms``, ``discord``, ``whatsapp`` — work when
      configured: keys/targets come from config, and the messaging adapters are
      built on demand by ``adapter_setup.ensure_registered`` (written for exactly
      the "CLI / cron subprocess starts empty" case).
    * ``matrix`` — **cannot** deliver detached: it needs a channel object from
      ``NotificationManager`` and has no direct fallback, so the router answers
      ``False, "matrix not configured"``. Delivered by the gateway only.
    """
    data = dict(payload or {})

    if event_queue is None:
        # Probe ``sys.modules`` instead of importing: the queue's process-global is
        # set by ``SystemEventQueue.__init__``, which can only have run if this very
        # module was imported — and importing ``navig.gateway.system_events`` runs
        # the whole ``navig.gateway`` package (its __init__ pulls in the server),
        # which a detached child with no queue should not have to pay for.
        module = sys.modules.get("navig.gateway.system_events")
        if module is not None:
            event_queue = module.get_system_events()

    if event_queue is not None:
        return await event_queue.emit(event_type, data)

    spec = SYSTEM_EVENT_SPECS.get(event_type)
    title, body, priority = _render(event_type, data)
    if spec is not None and spec.recovery:
        data.setdefault("tidy", "status")
    data.setdefault("event_type", event_type)

    from navig.notify import dispatch

    return await dispatch("system_alert", title, body, priority=priority, data=data)
