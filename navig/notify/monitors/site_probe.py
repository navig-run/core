"""Site probe monitor — scheduled uptime + TLS certificate expiry (opt-in).

Where ``monitors.host_checks`` thresholds THIS machine's resources and hosts,
this monitor watches **URLs**: every poll runs the existing
:func:`navig.monitoring.checks.collect_http` probe (the same ``SiteCheckTool``
the CLI / monitoring API uses — no HTTP logic is duplicated here) for each
configured URL and emits a system event per edge — ``site_down`` → ``site_up``
for availability, ``cert_expiry_warning`` / ``cert_expired`` → ``cert_ok`` for
the certificate ladder. The 1c event→notify bridge turns each event into a
``system_alert`` with its own latch/cooldown/delivery gates, so nothing here
re-implements dedup.

**Down is defined precisely.** A probe result means the site is down when:

* the probe itself failed — connection refused/reset, DNS failure, timeout, too
  many redirects, or an SSRF-policy block (all arrive as a ``CheckFailure``);
  the reason rides in the alert body;
* the request completed and ``online`` is false (``SiteCheckTool`` sets it true
  only for 2xx/3xx — a 4xx/5xx answers "not up"); or
* a status code ≥ 500 was returned even when the ``online`` flag is missing.

A target that produced no readings and no failure is skipped — absence of data
is not an outage (the same rule as host_health's collector gaps).

**The SSRF guard stays on, deliberately.** The URLs come from operator config,
but config can be written by lower-privilege paths (``navig config set`` from a
bot command, an imported config file, a shared workspace), so every probe still
runs through ``SiteCheckTool``'s ``policy_from_config`` check: a URL aimed at
the cloud metadata endpoint or an internal host is blocked and surfaces as a
probe failure carrying "blocked by SSRF policy", instead of the monitor
becoming an internal scanner on demand. Tests that probe a local server patch
``navig.tools.site_check.check_url`` (the same patch the checks tests use).

Config (``monitors.site_probes``, **default OFF** — opt-in, same posture as
``host_checks``)::

    monitors:
      site_probes:
        enabled: false
        urls:
          - url: https://example.com
            name: example           # label for events / alert_state (default: netloc)
            cert_warn_days: 14      # days-to-expiry ≤ these → warning / expired
            cert_crit_days: 3
        poll_seconds: 600
        maintenance:                # dispatch-suppressed windows (local time);
          - {start: "02:00", end: "04:00",  # `hosts` entries match the probe
             days: [6], hosts: [example]}   # label (name or netloc), not the URL

Read the same way the resources / host_checks monitors read their config: the
gateway passes ``monitors.site_probes`` as a plain dict and this module applies
defaults. Maintenance windows, their parsing and their log lines are shared
with ``host_health`` — identical semantics, one implementation.

Cert escalation and restarts
----------------------------
Warning and expired share one ``alert_state`` bucket ``(label, "cert")`` — the
recovery ``cert_ok`` clears whichever is latched. A warning that crosses into
expiry is dispatched through the bridge's escalation rule (the latched
``cert_expiry_warning`` declares ``cert_expired`` as an escalation), so the
operator is actually told the certificate died. Trackers are seeded from
``alert_state`` at start: a daemon restart while something is still firing does
not re-announce it. During a maintenance window sampling and tracker updates
continue but emits are suppressed; the unannounced edge is remembered in-memory
(``state.pending``) and settled on the first poll after the window — an edge
that both began and resolves inside the window announces nothing.
``alert_state`` rows are written only by the bridge, on VERIFIED dispatch.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from navig.monitoring.checks import _netloc, collect_http
from navig.notify.monitors.host_health import (
    MaintenanceWindow,
    in_maintenance,
    parse_maintenance,
)
from navig.notify.monitors.resources import Thresholds, ThresholdTracker

logger = logging.getLogger("navig.notify")

#: Default poll cadence (seconds) — sites are sampled on a timer, not a hot path.
DEFAULT_POLL_S = 600.0

#: Floor for ``poll_seconds`` — a misconfigured ``0`` must not become a hot loop.
#: (A module constant so a loop-survival test can monkeypatch it.)
_MIN_POLL_S = 1.0

#: Per-URL defaults for the certificate ladder (overridable per entry).
DEFAULT_WARN_DAYS = 14
DEFAULT_CRIT_DAYS = 3

#: Band for the binary site tracker: 1 = down, 0 = up. ``update(1)`` fires,
#: ``update(0)`` clears — the same hysteresis code path host_health uses.
_STATE_BAND = Thresholds(high=0.5, low=0.0, sustain=1)

#: Cert tracker key → the alert event it emits (both clear via ``cert_ok``).
CERT_ALERT_EVENTS: dict[str, str] = {
    "cert_warn": "cert_expiry_warning",
    "cert_expired": "cert_expired",
}

#: The recovery both cert trackers share (one ``alert_state`` bucket per URL).
CERT_OK_EVENT = "cert_ok"


# ── config ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SiteTarget:
    """One configured URL: what to probe, what to call it, cert bands."""

    url: str
    label: str  # event / alert_state / maintenance host key (name or netloc)
    warn_days: float
    crit_days: float


def _band_days(raw: object, default: float, name: str, index: int) -> float:
    """One cert band from config — malformed or negative values fall back loudly."""
    if raw is None:
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "site probe monitor: urls entry %d has a non-numeric %s (%r) — using %s",
            index,
            name,
            raw,
            default,
        )
        return float(default)
    if value < 0:
        logger.warning(
            "site probe monitor: urls entry %d has a negative %s (%r) — using %s",
            index,
            name,
            raw,
            default,
        )
        return float(default)
    return value


def _targets_from(raw: object) -> list[SiteTarget]:
    """Config ``urls`` → targets. Accepts bare strings or mappings; bad entries
    are dropped loudly (a silently-unprobed URL is the failure this monitor is
    here to prevent). Duplicate labels are skipped — the label is the
    ``alert_state`` / tracker key, so two URLs under one name would collide."""
    targets: list[SiteTarget] = []
    if raw is None:
        return targets
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        logger.warning("site probe monitor: urls must be a list — ignored: %r", raw)
        return targets
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        if isinstance(entry, (str, bytes)):
            entry = {"url": str(entry)}
        if not isinstance(entry, Mapping):
            logger.warning("site probe monitor: urls entry %d is not a mapping/string", index)
            continue
        url = str(entry.get("url") or "").strip()
        if not url:
            logger.warning("site probe monitor: urls entry %d has no url — skipped", index)
            continue
        label = str(entry.get("name") or "").strip() or _netloc(url)
        if label in seen:
            logger.warning(
                "site probe monitor: duplicate label %r (urls entry %d) — skipped",
                label,
                index,
            )
            continue
        seen.add(label)
        warn_days = _band_days(entry.get("cert_warn_days"), DEFAULT_WARN_DAYS, "cert_warn_days", index)
        crit_days = _band_days(entry.get("cert_crit_days"), DEFAULT_CRIT_DAYS, "cert_crit_days", index)
        if crit_days > warn_days:
            logger.warning(
                "site probe monitor: urls entry %d cert_crit_days (%s) > cert_warn_days "
                "(%s) — clamped to the warn band",
                index,
                crit_days,
                warn_days,
            )
            crit_days = warn_days
        targets.append(
            SiteTarget(url=url, label=label, warn_days=warn_days, crit_days=crit_days)
        )
    return targets


def _now() -> datetime:
    """Local wall clock — the only clock maintenance windows are expressed against."""
    return datetime.now()


# ── state ──────────────────────────────────────────────────────────────────────


@dataclass
class MonitorState:
    """Everything one monitor run carries between polls."""

    targets: list[SiteTarget]
    #: ``(label, tracker key)`` → edge detector. Tracker keys: ``"site"``,
    #: ``"cert_warn"``, ``"cert_expired"``.
    trackers: dict[tuple[str, str], ThresholdTracker]
    #: Edges that occurred inside a maintenance window, waiting for the window
    #: to end: ``"alert"`` or ``"clear"`` (latest edge wins).
    pending: dict[tuple[str, str], str]
    #: Tracker keys whose alert THIS run announced (or inherited from a firing
    #: ``alert_state`` row) — a recovery is only announced for these.
    emitted: set[tuple[str, str]] = field(default_factory=set)
    windows: list[MaintenanceWindow] = field(default_factory=list)
    #: The gateway's SystemEventQueue (or ``None`` → emit_or_notify's detached
    #: fallback). The bridge must be installed on it for events to notify.
    queue: object | None = None
    poll_s: float = DEFAULT_POLL_S
    #: Lazy ``TriggerManager`` for the edge feed — cloned from host_health:
    #: built on first edge, and latched unavailable after a construction failure
    #: so a broken trigger store is logged once instead of once per poll.
    trigger_manager: object | None = None
    triggers_unavailable: bool = False


def build_state(
    config: dict | None = None, *, event_queue: object | None = None
) -> MonitorState:
    """Config dict → a seeded :class:`MonitorState` (trackers primed from ``alert_state``)."""
    cfg = config or {}
    targets = _targets_from(cfg.get("urls"))

    trackers: dict[tuple[str, str], ThresholdTracker] = {}
    for target in targets:
        trackers[(target.label, "site")] = ThresholdTracker(_STATE_BAND)
        # The cert countdown is read INVERTED (``-days_left``) so ThresholdTracker's
        # "value ≥ high fires, value ≤ low clears" alerts when days ≤ band and
        # clears when days > band. high == low makes that boundary exact — there
        # is no hysteresis gap to guess at on an integer day count.
        trackers[(target.label, "cert_warn")] = ThresholdTracker(
            Thresholds(high=-target.warn_days, low=-target.warn_days, sustain=1)
        )
        trackers[(target.label, "cert_expired")] = ThresholdTracker(
            Thresholds(high=-target.crit_days, low=-target.crit_days, sustain=1)
        )

    poll_s = float(cfg.get("poll_seconds", DEFAULT_POLL_S) or DEFAULT_POLL_S)
    state = MonitorState(
        targets=targets,
        trackers=trackers,
        pending={},
        windows=parse_maintenance(cfg.get("maintenance")),
        queue=event_queue,
        poll_s=max(_MIN_POLL_S, poll_s),
    )
    _seed_from_alert_state(state)
    return state


def _seed_from_alert_state(state: MonitorState) -> None:
    """Prime trackers from the bridge's latch so a restart does not re-alert."""
    try:
        from navig.notify.store import load_alert_states  # noqa: PLC0415

        firing = {
            key: row
            for key, row in load_alert_states().items()
            if row.get("state") == "firing"
        }
    except Exception:  # noqa: BLE001 — a missing store must not block boot
        logger.warning("site probe monitor: alert_state seed failed", exc_info=True)
        return
    if not firing:
        return
    labels = {target.label for target in state.targets}
    for (label, bucket), row in firing.items():
        if label not in labels:
            continue
        if bucket == "site":
            keys = [(label, "site")]
        elif bucket == "cert":
            if str(row.get("event_type") or "") == "cert_expiry_warning":
                keys = [(label, "cert_warn")]
            else:
                # Expired, or a legacy row that does not say which band latched:
                # seed BOTH so neither re-announces; the shared recovery clears
                # whichever is actually outstanding.
                keys = [(label, "cert_warn"), (label, "cert_expired")]
        else:
            continue
        for key in keys:
            tracker = state.trackers.get(key)
            if tracker is not None:
                tracker.alerted = True
                state.emitted.add(key)


# ── sample interpretation ──────────────────────────────────────────────────────


def _status_int(latest: Mapping[str, object]) -> int | None:
    sample = latest.get("http.status_code")
    if sample is None:
        return None
    try:
        return int(float(str(getattr(sample, "value", sample)).strip()))
    except (TypeError, ValueError):
        return None


def _online_flag(latest: Mapping[str, object]) -> bool | None:
    sample = latest.get("http.online")
    if sample is None:
        return None
    return str(getattr(sample, "value", sample)).strip().lower() in ("1", "true", "yes")


def _expiry_date(text: str) -> date | None:
    """``"YYYY-MM-DD"`` (or an OpenSSL-style ``notAfter``) → a date; ``None`` if unparseable."""
    cleaned = str(text).strip()
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%b %d %H:%M:%S %Y %Z"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


# ── one poll ───────────────────────────────────────────────────────────────────


async def poll_once(state: MonitorState) -> None:
    """Probe every configured URL once and settle the resulting edges."""
    now = _now()
    for target in state.targets:
        try:
            samples, failures = await collect_http(
                [target.url], host=target.label, checks=("http", "cert")
            )
        except Exception:  # noqa: BLE001 — one dead target must not stop the sweep
            logger.warning(
                "site probe monitor: probe failed for %r", target.url, exc_info=True
            )
            continue
        maint = in_maintenance(now, state.windows, target.label)
        latest = {sample.metric: sample for sample in samples}
        await _react_site(state, target, latest, failures, maint)
        await _react_cert(state, target, latest, maint)


async def _react_site(
    state: MonitorState,
    target: SiteTarget,
    latest: Mapping[str, object],
    failures: Sequence[object],
    maint: bool,
) -> None:
    """Evaluate availability for one target: a failure, a non-2xx/3xx answer, or a 5xx."""
    label = target.label
    down = False
    reason: str | None = None
    if failures:
        down = True
        reason = str(getattr(failures[0], "reason", "") or "site check failed")
    elif "http.online" in latest or "http.status_code" in latest:
        status = _status_int(latest)
        if _online_flag(latest) is False:
            down = True
            reason = f"HTTP {status}" if status is not None else "not online"
        elif status is not None and status >= 500:
            down = True
            reason = f"HTTP {status}"
    else:
        # No reading and no failure — nothing was learned about this target, so
        # no tracker update (absence of data is not an outage).
        return

    edge = state.trackers[(label, "site")].update(1.0 if down else 0.0)
    alert_payload: dict = {"host": label, "url": target.url}
    if down and reason:
        alert_payload["reason"] = reason
    await _react(
        state,
        label,
        "site",
        edge,
        alert=("site_down", alert_payload),
        clear=("site_up", {"host": label, "url": target.url}),
        maint=maint,
    )


async def _react_cert(
    state: MonitorState, target: SiteTarget, latest: Mapping[str, object], maint: bool
) -> None:
    """Evaluate the certificate ladder: warn band first, then the crit (expired) band."""
    sample = latest.get("http.cert_expiry")
    if sample is None:
        return  # plain HTTP, a non-TLS site, or a failed handshake — no reading, no edge
    expiry = _expiry_date(str(getattr(sample, "value", sample)))
    if expiry is None:
        logger.debug(
            "site probe monitor: unparseable cert_expiry %r for %s",
            getattr(sample, "value", sample),
            target.label,
        )
        return
    days = (expiry - date.today()).days
    payload = {
        "host": target.label,
        "url": target.url,
        "days": days,
        "expiry": expiry.isoformat(),
    }
    for key, alert_event in CERT_ALERT_EVENTS.items():
        edge = state.trackers[(target.label, key)].update(-float(days))
        await _react(
            state,
            target.label,
            key,
            edge,
            alert=(alert_event, payload),
            clear=(CERT_OK_EVENT, payload),
            maint=maint,
        )


async def _feed_triggers(
    state: MonitorState, label: str, tracker_key: str, edge: str, payload: dict
) -> None:
    """Best-effort: hand the edge to ``TriggerManager`` as a THRESHOLD event.

    Cloned from host_health's ``_feed_threshold_trigger`` — a broken trigger
    store (or a trigger's exploding action) is logged and the monitor moves
    on: the probe loop and the notification path must not die with it.
    ``source="site_probes"`` so a trigger can tell uptime edges from host ones.
    """
    if state.trigger_manager is None:
        if state.triggers_unavailable:
            return
        try:
            from navig.commands.triggers import TriggerManager  # noqa: PLC0415

            state.trigger_manager = TriggerManager()
        except Exception:  # noqa: BLE001 — construction failure: log once, not per poll
            state.triggers_unavailable = True
            logger.warning("site probe monitor: trigger feed unavailable", exc_info=True)
            return
    try:
        from navig.commands.triggers import TriggerEvent, TriggerType  # noqa: PLC0415

        data: dict = {"host": label, "metric": tracker_key, "state": edge}
        value = payload.get("value")
        if value is not None:
            data["value"] = value
        event = TriggerEvent(type=TriggerType.THRESHOLD, source="site_probes", data=data)
        await asyncio.to_thread(state.trigger_manager.process_event, event)
    except Exception:  # noqa: BLE001 — a bad action must not disable the feed or the poll
        logger.warning("site probe monitor: trigger feed failed", exc_info=True)


async def _react(
    state: MonitorState,
    label: str,
    tracker_key: str,
    edge: str | None,
    *,
    alert: tuple[str, dict],
    clear: tuple[str, dict],
    maint: bool,
) -> None:
    """Settle one tracker key: maintenance bookkeeping, emit, retry, pair-clear.

    Order matters, cloned from host_health:

    1. a **pending edge** from inside a window is settled first (only when the
       window has ended — a pending alert that already resolved inside the window
       announces nothing);
    2. inside a window the edge is recorded but never emitted;
    3. a fresh edge emits — unless the tracker is latched but nothing was ever
       recorded as announced (a failed emit), which retries here;
    4. a clear only speaks for keys THIS run announced (or seeded as firing).
    """
    key = (label, tracker_key)
    tracker = state.trackers[key]

    pending = state.pending.get(key)
    if maint:
        if edge in ("alert", "clear"):
            state.pending[key] = edge
        return
    if edge in ("alert", "clear"):
        # One feed per tracker transition (the tracker only returns an edge on a
        # real change), never on the latched retry below. An edge swallowed by a
        # maintenance window is not fed either — the window mutes the response,
        # not just the card. Best-effort: a bad trigger store or a raising action
        # can never stop the probe loop (mirrors host_health's feed).
        payload = (alert if edge == "alert" else clear)[1]
        await _feed_triggers(state, label, tracker_key, edge, payload)
    if pending is not None:
        state.pending.pop(key, None)
        if pending == "alert":
            if edge == "clear":
                return  # began and ended during the window: nothing to announce
            if await _emit(state, *alert):
                state.emitted.add(key)
            return
        # pending == "clear": only worth announcing if the alert went out.
        if key in state.emitted and await _emit(state, *clear):
            state.emitted.discard(key)

    if edge == "alert":
        if await _emit(state, *alert):
            state.emitted.add(key)
    elif edge == "clear":
        if key in state.emitted and await _emit(state, *clear):
            state.emitted.discard(key)
    elif tracker.alerted and key not in state.emitted:
        # Latched but never recorded as announced (the emit itself failed on an
        # earlier poll): retry while the condition holds. The bridge's cooldown
        # and latch still gate how often that can actually reach a channel.
        if await _emit(state, *alert):
            state.emitted.add(key)


async def _emit(state: MonitorState, event_type: str, payload: dict) -> bool:
    """Emit one system event. True when it was handed off (or verified delivered)."""
    from navig.notify.delivery import all_channels_failed  # noqa: PLC0415
    from navig.notify.producers.system_events_bridge import emit_or_notify  # noqa: PLC0415

    outcome = await emit_or_notify(event_type, payload, event_queue=state.queue)
    if isinstance(outcome, dict) and all_channels_failed(outcome):
        # Detached fallback path, every channel failed: do not record the
        # announcement — the retry branch above re-fires next poll (the same
        # rollback the resources monitor does on a verified all-failed dispatch).
        logger.warning(
            "site probe monitor: %s reached no channel — will retry", event_type
        )
        return False
    return True


# ── the loop ───────────────────────────────────────────────────────────────────


async def run_site_probe_monitor(
    config: dict | None = None, *, event_queue: object | None = None
) -> None:
    """Background task: probe configured URLs and emit edges into the event queue."""
    state = build_state(config, event_queue=event_queue)
    if not state.targets:
        # Nothing configured = nothing to probe: exit instead of idling a task
        # the deck would report as "running" (a cleared handle is honest).
        logger.info("site probe monitor: no URLs configured — idle (monitors.site_probes.urls)")
        return
    logger.info(
        "site probe monitor started (targets=%s, poll=%ss)",
        [target.label for target in state.targets],
        state.poll_s,
    )
    try:
        while True:
            # A bad poll must NOT kill the monitor — otherwise it dies silently
            # while the deck still shows it enabled. Log and keep looping.
            try:
                await poll_once(state)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad poll must not kill the monitor
                logger.warning("site probe monitor: poll failed, continuing", exc_info=True)
            await asyncio.sleep(state.poll_s)
    except asyncio.CancelledError:
        logger.info("site probe monitor stopped")
        raise
