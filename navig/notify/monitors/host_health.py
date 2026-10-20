"""Host health-check monitor — per-host thresholds with PAIRED CLEARS (Gate 1d).

Where ``monitors.resources`` samples THIS machine with psutil and dispatches
``system_alert`` directly, this monitor drives the **system-event path**: every
poll runs :func:`navig.monitoring.checks.collect_with_errors` for each configured
host (empty ``hosts`` = this machine only), detects threshold edges with the same
:class:`~navig.notify.monitors.resources.ThresholdTracker` hysteresis, and emits a
system event per edge — ``host_disk_warning`` → ``host_disk_ok``,
``host_memory_warning`` → ``host_memory_ok``, ``host_cpu_warning`` →
``host_cpu_ok``, ``service_down``/``service_up``, ``host_down``/``host_up``. The
1c event→notify bridge turns each event into a ``system_alert`` with its own
latch/cooldown/delivery gates, so nothing here re-implements dedup.

**Paired clears are the point of this branch.** A latch that only ever fires
sticks forever: every edge has a recovery event, emitted when the metric falls
back below its low band, when a watched service returns, or when a host answers
again. The bridge refuses a recovery with no outstanding alert, so a routine
healthy sample never chirps.

Config (``monitors.host_checks``, **default OFF** — opt-in first release)::

    monitors:
      host_checks:
        enabled: false
        hosts: []                 # empty = this machine only; names resolve via config
        poll_seconds: 300
        thresholds:
          disk_pct: {warn: 85, crit: 95}   # scalar warn is accepted too; defaults
          mem_pct:  {warn: 80, crit: 95}   # come from navig.core.thresholds REGISTRY
          cpu_pct:  {warn: 75, crit: 90}
        services: []              # watched units; empty = checks.CRITICAL_SERVICES
        maintenance:              # dispatch-suppressed windows (local time);
          - {start: "02:00", end: "04:00",  # days: weekday(), Monday=0; overnight
             days: [6], hosts: [nas]}        # ranges span midnight; hosts = labels

Read the same way the resources monitor reads its config: the gateway passes
``monitors.host_checks`` as a plain dict and this module applies defaults.

Latches and restarts
--------------------
Trackers are seeded from ``alert_state`` (1c's table) at start: a daemon restart
while something is still firing does not re-announce it. During a **maintenance
window** sampling and tracker updates continue but emits are suppressed; the
unannounced edge is remembered in-memory (``state.pending``) and settled on the
first poll after the window — an edge that both began and resolves inside the
window announces nothing. ``alert_state`` rows are written only by the bridge,
on VERIFIED dispatch (a row written here would make the bridge suppress the
post-window emit it exists to deliver).

CPU evidence and designed load
------------------------------
Two things make a *local* CPU edge trustworthy. **The monitor owns its window:**
``_apply_own_cpu_window`` replaces the collected reading with busy % between
this monitor's own ``cpu_times()`` snapshots, because
``psutil.cpu_percent(interval=None)`` on the daemon's shared thread pool
measures *whoever called it last* (dashboard, briefing, eyes) — never the poll
interval the ``sustain`` logic counts. **A held heavy lane is designed load:**
the slot protocol claims 24+ of 32 cores on purpose, so while a lane is held the
CPU edge parks in ``pending`` (maintenance semantics) instead of paging — a
spike that resolved during CI announces nothing, one still hot after the queue
drains announces then. Remote hosts never consult local lanes.

Trigger feed (best-effort)
--------------------------
Every edge is also handed to ``TriggerManager.process_event`` as a
``TriggerType.THRESHOLD`` event so operator-defined threshold automations (the
``triggers.py`` "disk space warning → cleanup script" case) fire off the same
sampling. Failures are logged and swallowed — the alert path never depends on it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from navig.monitoring.checks import CRITICAL_SERVICES, Sample, _local_label, collect_with_errors
from navig.notify.monitors.resources import Thresholds, ThresholdTracker

logger = logging.getLogger("navig.notify")

#: Default poll cadence (seconds) — hosts are sampled on a timer, not a hot path.
DEFAULT_POLL_S = 300.0

#: Floor for ``poll_seconds`` — a misconfigured ``0`` must not become a hot loop.
#: (A module constant so a loop-survival test can monkeypatch it.)
_MIN_POLL_S = 1.0

#: The checks every poll runs: ``resources`` carries disk_pct / mem_pct / cpu_pct,
#: ``services`` carries ``service.<name>.status``. Per-mount disk, network, http
#: and cert are not thresholded here — they belong to their own surfaces.
CHECKS: tuple[str, ...] = ("resources", "services")

#: metric → (warn event, recovery event, ``alert_state`` family, thresholds
#: registry name). The family is the bridge's latch bucket — the recovery must
#: share it or the clear never lifts the alert.
METRIC_EVENTS: dict[str, tuple[str, str, str, str]] = {
    "disk_pct": ("host_disk_warning", "host_disk_ok", "disk", "disk_usage"),
    "mem_pct": ("host_memory_warning", "host_memory_ok", "memory", "memory_usage"),
    "cpu_pct": ("host_cpu_warning", "host_cpu_ok", "cpu", "cpu_usage"),
}

#: Service states that mean "down". ``not-installed`` / ``unknown`` are NOT down:
#: a unit the host does not have (or a probe that failed) is not an outage.
SERVICE_DOWN_STATES = frozenset({"inactive", "failed", "dead", "stopped"})

#: Band for the binary trackers (host unreachable / one service): 1 = bad, 0 = fine.
#: ``update(1)`` fires, ``update(0)`` clears — same hysteresis code path as metrics.
_STATE_BAND = Thresholds(high=0.5, low=0.0, sustain=1)

#: A local probe that failed because psutil is missing is a GATHERING gap, not a
#: dead host — treating it as unreachable would cry ``host_down`` every poll on a
#: psutil-less install. Matched against ``CheckFailure`` reasons (the checks
#: module raises with "psutil" in the message for every local collector gap).
_PSUTIL_GAP = "psutil"


# ── config ─────────────────────────────────────────────────────────────────────


def _thresholds_from(
    raw: object, registry_name: str, *, sustain: int = 1
) -> tuple[Thresholds, float]:
    """``(tracker band, crit level)`` from one config value + registry defaults.

    ``raw`` is ``{warn, crit}``, a bare number (warn only), or ``None`` (defaults
    from ``navig.core.thresholds`` REGISTRY — display thresholds reused as alert
    defaults, never invented here).
    """
    from navig.core.thresholds import resolve

    registry = resolve(registry_name)
    warn, crit = float(registry.warn_pct), float(registry.crit_pct)
    if isinstance(raw, Mapping):
        if raw.get("warn") is not None:
            warn = float(raw["warn"])
        if raw.get("crit") is not None:
            crit = float(raw["crit"])
    elif raw is not None:
        warn = float(raw)
    band = Thresholds(high=warn, low=max(0.0, warn - 10.0), sustain=sustain)
    return band, crit


@dataclass(frozen=True)
class MaintenanceWindow:
    """A dispatch-suppressed window in LOCAL time (``HH:MM`` ranges)."""

    start: int  # minutes since local midnight
    end: int  # exclusive; ``start > end`` spans midnight
    days: frozenset[int] | None = None  # None = every day; weekday(), Monday=0
    hosts: frozenset[str] | None = None  # None = every host
    label: str = ""  # the original text, for logs


def _hhmm(text: object) -> int | None:
    """``"HH:MM"`` → minutes since midnight; ``None`` when malformed."""
    try:
        hour_s, _, minute_s = str(text).partition(":")
        hour, minute = int(hour_s), int(minute_s)
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def parse_maintenance(raw: object) -> list[MaintenanceWindow]:
    """Config ``maintenance`` list → windows; malformed entries are dropped loudly."""
    windows: list[MaintenanceWindow] = []
    if raw is None:
        return windows
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        logger.warning("host check monitor: maintenance must be a list — ignored: %r", raw)
        return windows
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            logger.warning("host check monitor: maintenance entry %d is not a mapping", index)
            continue
        start = _hhmm(entry.get("start"))
        end = _hhmm(entry.get("end"))
        if start is None or end is None:
            logger.warning(
                "host check monitor: maintenance entry %d needs HH:MM start/end — skipped",
                index,
            )
            continue
        days_raw = entry.get("days")
        days = (
            frozenset(int(d) for d in days_raw)
            if isinstance(days_raw, (list, tuple)) and days_raw
            else None
        )
        hosts_raw = entry.get("hosts")
        hosts = (
            frozenset(str(h) for h in hosts_raw)
            if isinstance(hosts_raw, (list, tuple)) and hosts_raw
            else None
        )
        windows.append(
            MaintenanceWindow(
                start=start,
                end=end,
                days=days,
                hosts=hosts,
                label=f"{entry.get('start')}-{entry.get('end')}",
            )
        )
    return windows


def _now() -> datetime:
    """Local wall clock — the only clock maintenance windows are expressed against."""
    return datetime.now()


def in_maintenance(now: datetime, windows: Sequence[MaintenanceWindow], host: str) -> bool:
    """Whether *host* is inside a window at *now* (local time, overnight spans OK)."""
    minutes = now.hour * 60 + now.minute
    day = now.weekday()
    for window in windows:
        if window.hosts is not None and host not in window.hosts:
            continue
        if window.days is not None and day not in window.days:
            continue
        if window.start == window.end:
            continue  # zero-length: never active (an empty range is not "all day")
        if window.start < window.end:
            active = window.start <= minutes < window.end
        else:  # spans midnight: 23:00–07:00
            active = minutes >= window.start or minutes < window.end
        if active:
            return True
    return False


def _label(host: str | None) -> str:
    """The sample label ``collect_with_errors`` will use for this target.

    This IS ``checks._local_label`` — the collector's own naming function — so a
    maintenance window's ``hosts`` filter, the trackers, and the ``alert_state``
    rows all key off exactly the name the samples carry.
    """
    return _local_label(host)


def _could_be_local(host: str | None) -> bool:
    if host is None:
        return True
    from navig.monitoring.checks import _is_local_name  # noqa: PLC0415

    return _is_local_name(host)


# ── CPU evidence: the monitor's own window, and designed CI load ──────────────


def _cpu_times_sample() -> Any | None:
    """One snapshot of cumulative CPU times — a window edge only we advance.

    ``None`` only when psutil is unavailable; windowing then stays off, exactly
    like the monitor itself idles without psutil. Never raises: a monitor that
    cannot read CPU keeps its last evidence rather than losing the poll.
    """
    try:
        import psutil  # noqa: PLC0415 — core dependency, deferred for `navig help` speed
    except Exception:  # noqa: BLE001
        return None
    try:
        return psutil.cpu_times()
    except Exception:  # noqa: BLE001
        return None


def _busy_pct(t1: Any, t2: Any) -> float | None:
    """Busy % between two ``cpu_times()`` snapshots, or ``None`` on no window.

    Mirrors psutil's own ``_cpu_tot_time``/``_cpu_busy_time`` math — trim negative
    deltas (clock quirks), subtract ``guest*`` from the total (Linux counts them
    inside ``user``/``nice``) and ``idle``/``iowait`` from busy — but from OUR
    snapshots: pure reads, immune to every other ``cpu_percent(interval=None)``
    caller in the process. Duck-typed on ``_fields`` so the platform's own
    field set (Windows has no ``guest``/``iowait``) decides.
    """
    deltas: dict[str, float] = {}
    for field_name in t1._fields:
        deltas[field_name] = max(0.0, getattr(t2, field_name) - getattr(t1, field_name))
    total = sum(deltas.values()) - deltas.get("guest", 0.0) - deltas.get("guest_nice", 0.0)
    if total <= 0:
        return None
    busy = total - deltas.get("idle", 0.0) - deltas.get("iowait", 0.0)
    return round(100.0 * busy / total, 1)


def _apply_own_cpu_window(state: MonitorState, samples: list[Sample]) -> list[Sample]:
    """Replace the LOCAL CPU reading with busy % over the monitor's OWN window.

    ``monitor.get_cpu_info()`` reads ``psutil.cpu_percent(interval=None)`` —
    *since the last call on whichever pool thread it landed on*. The deck
    dashboard (8 s TTL), the briefing route and the agent eyes call that same
    sampler through ``asyncio.to_thread`` on the daemon's shared pool, so the
    window a monitor poll inherits is whoever ran last — seconds, minutes, or
    psutil's stale module fallback — never the poll interval its ``sustain``
    logic assumes (3 reads were meant to prove "15 min sustained").

    First poll has no baseline yet: the shared reading passes through
    (``sustain=3`` means one unowned reading can never alert alone). Remote
    (``source="ssh"``) samples keep their transport's own semantics. A value
    that cannot be computed keeps the collected one — never a hole.
    """
    if not state.window_cpu or state.cpu_prev is None:
        return samples
    now = _cpu_times_sample()
    if now is None:
        return samples
    out: list[Sample] = []
    for sample in samples:
        if sample.metric == "cpu_pct" and sample.source == "local":
            pct = _busy_pct(state.cpu_prev, now)
            out.append(replace(sample, value=pct) if pct is not None else sample)
        else:
            out.append(sample)
    return out


def _heavy_lanes_busy(*, is_alive: Callable[[int], bool] | None = None) -> bool:
    """Is a heavy-slot lane held on THIS machine — CI saturating the box is BY DESIGN.

    The slot protocol (``scripts/heavy-slot-lib.mjs``) lets 4 lanes × 6 cores
    claim 24 of 32 cores, so a *local* CPU edge while a lane is held reports the
    machine doing exactly what it was asked to do — not an anomaly. The
    protocol's constants and path come from ``navig.commands.repo`` (one Python
    reader; never fork them); the liveness rule mirrors heavy-slot-lib's
    ``isFree()``: a holder file named by ``_GATE_LANE_RE`` (``slot``/``overflow``/
    ``express``/``push`` — a ticket holds no lane), younger than
    ``GATE_MAX_HOLD_S``, naming a live pid — via ``psutil.pid_exists``, NEVER
    ``os.kill(pid, 0)``: on Windows signal 0 is ``TerminateProcess``, it would
    kill the gate it just asked about (the trap ``core/conftest.py`` documents).

    Fails open to ALERTING: no psutil, unreadable dir/file, malformed JSON →
    not busy → CPU alerts flow exactly as before. Never raises.
    """
    # The protocol's one Python reader — constants and path come from repo.py, never forked.
    from navig.commands.repo import _GATE_LANE_RE, GATE_MAX_HOLD_S, _gate_slot_dir  # noqa: PLC0415

    probe = is_alive
    if probe is None:
        try:
            import psutil  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return False
        probe = psutil.pid_exists
    try:
        files = [f for f in _gate_slot_dir().iterdir() if _GATE_LANE_RE.match(f.name)]
    except OSError:
        return False
    now = datetime.now(timezone.utc)
    for f in files:
        try:
            holder = json.loads(f.read_text(encoding="utf-8"))
            pid = int(holder["pid"])
            started = datetime.fromisoformat(str(holder["started"]).replace("Z", "+00:00"))
            age_s = (now - started).total_seconds()
        except (OSError, ValueError, KeyError, TypeError):
            continue  # unreadable or mid-write: heavy-slot-lib treats it as free too
        if age_s > GATE_MAX_HOLD_S:
            continue  # a stale file must never silence alerts forever
        if pid > 0 and probe(pid):
            return True
    return False


# ── state ──────────────────────────────────────────────────────────────────────


@dataclass
class MonitorState:
    """Everything one monitor run carries between polls."""

    #: ``(configured host or None, sample label)`` per target.
    targets: list[tuple[str | None, str]]
    #: metric → the warn band the tracker alerts on.
    thresholds: dict[str, Thresholds]
    #: metric → crit level (payload ``level`` only; the tracker alerts at warn).
    crit: dict[str, float]
    #: ``(label, tracker key)`` → edge detector. Tracker keys: a metric name,
    #: ``"host"``, or ``"service:<name>"``.
    trackers: dict[tuple[str, str], ThresholdTracker]
    #: Edges that occurred inside a maintenance window, waiting for the window to
    #: end: ``"alert"`` or ``"clear"`` (latest edge wins).
    pending: dict[tuple[str, str], str]
    #: Tracker keys whose alert THIS run announced (or inherited from a firing
    #: ``alert_state`` row) — a recovery is only announced for these.
    emitted: set[tuple[str, str]] = field(default_factory=set)
    #: Units watched for down/up; ``service.<name>.status`` samples outside this
    #: set are ignored (a box has hundreds of stopped services that are fine).
    watched: frozenset[str] = frozenset()
    windows: list[MaintenanceWindow] = field(default_factory=list)
    #: The gateway's SystemEventQueue (or ``None`` → emit_or_notify's detached
    #: fallback). The bridge must be installed on it for events to notify.
    queue: object | None = None
    poll_s: float = DEFAULT_POLL_S
    #: Lazily built; unavailable flag stops a per-poll construction-retry storm.
    trigger_manager: object | None = None
    triggers_unavailable: bool = False
    #: The monitor's OWN ``psutil.cpu_times()`` snapshot — the near edge of its
    #: CPU window. ``None`` until the first poll closes it; see
    #: :func:`_apply_own_cpu_window` for why the window must be ours.
    cpu_prev: Any = None
    #: Test seam: ``False`` keeps collected CPU samples untouched — a fake
    #: ``collect`` must never read this machine's real CPU into a test.
    window_cpu: bool = True


def build_state(
    config: dict | None = None, *, event_queue: object | None = None
) -> MonitorState:
    """Config dict → a seeded :class:`MonitorState` (trackers primed from ``alert_state``)."""
    cfg = config or {}
    hosts = [str(h) for h in (cfg.get("hosts") or [])] or [None]
    targets = [(host, _label(host)) for host in hosts]

    thresholds: dict[str, Thresholds] = {}
    crit: dict[str, float] = {}
    thresholds_cfg = cfg.get("thresholds") or {}
    for metric, (_warn_ev, _ok_ev, _family, registry_name) in METRIC_EVENTS.items():
        raw = thresholds_cfg.get(metric) if isinstance(thresholds_cfg, Mapping) else None
        # CPU needs sustain so a momentary spike is ignored (same rule as resources).
        band, level = _thresholds_from(
            raw, registry_name, sustain=3 if metric == "cpu_pct" else 1
        )
        thresholds[metric] = band
        crit[metric] = level

    watched_raw = cfg.get("services") or CRITICAL_SERVICES
    watched = frozenset(str(name) for name in watched_raw)

    poll_s = float(cfg.get("poll_seconds", DEFAULT_POLL_S) or DEFAULT_POLL_S)
    trackers: dict[tuple[str, str], ThresholdTracker] = {}
    for _host, label in targets:
        for metric in METRIC_EVENTS:
            trackers[(label, metric)] = ThresholdTracker(thresholds[metric])
        trackers[(label, "host")] = ThresholdTracker(_STATE_BAND)
        for name in watched:
            trackers[(label, f"service:{name}")] = ThresholdTracker(_STATE_BAND)

    state = MonitorState(
        targets=targets,
        thresholds=thresholds,
        crit=crit,
        trackers=trackers,
        pending={},
        watched=watched,
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
            key for key, row in load_alert_states().items() if row.get("state") == "firing"
        }
    except Exception:  # noqa: BLE001 — a missing store must not block boot
        logger.warning("host check monitor: alert_state seed failed", exc_info=True)
        return
    if not firing:
        return
    labels = {label for _host, label in state.targets}
    family_to_metric = {family: metric for metric, (_w, _o, family, _r) in METRIC_EVENTS.items()}
    for (host, bucket) in firing:
        if host not in labels:
            continue
        if bucket == "host":
            keys = [(host, "host")]
        elif bucket == "service":
            # The bridge latches ALL services per host under one bucket, so a
            # firing row means "we already said something is down here" — seed
            # every watched service tracker to suppress a re-announce storm.
            keys = [(host, f"service:{name}") for name in state.watched]
        elif bucket in family_to_metric:
            keys = [(host, family_to_metric[bucket])]
        else:
            continue
        for key in keys:
            tracker = state.trackers.get(key)
            if tracker is not None:
                tracker.alerted = True
                state.emitted.add(key)


# ── one poll ───────────────────────────────────────────────────────────────────


async def poll_once(state: MonitorState) -> None:
    """Sample every target once and settle the resulting edges."""
    now = _now()
    for config_host, label in state.targets:
        try:
            samples, failures = await collect_with_errors(config_host, CHECKS)
        except Exception:  # noqa: BLE001 — one dead target must not stop the sweep
            logger.warning(
                "host check monitor: collect failed for %r", config_host, exc_info=True
            )
            continue
        samples = _apply_own_cpu_window(state, samples)
        maint = in_maintenance(now, state.windows, label)
        if not samples:
            if not failures:
                continue
            reason = failures[0].reason
            if all(_PSUTIL_GAP in failure.reason for failure in failures):
                # Gathering gap on this machine (no psutil) — not an outage.
                logger.debug("host check monitor: local probe gap on %s: %s", label, reason)
                continue
            edge = state.trackers[(label, "host")].update(1.0)
            await _react(
                state,
                label,
                "host",
                edge,
                alert=("host_down", {"host": label, "reason": reason}),
                clear=("host_up", {"host": label}),
                value=None,
                maint=maint,
            )
            continue
        # Reachable (at least one reading) → clear an outstanding unreachable edge.
        edge = state.trackers[(label, "host")].update(0.0)
        await _react(
            state,
            label,
            "host",
            edge,
            alert=("host_down", {"host": label}),
            clear=("host_up", {"host": label}),
            value=None,
            maint=maint,
        )
        for failure in failures:
            # Partial failure (e.g. services probe dead but resources fine): keep
            # the readings we did get — a partial sample is not an outage.
            logger.debug(
                "host check monitor: %s check on %s dropped: %s",
                failure.check,
                failure.host,
                failure.reason,
            )
        # Designed load: while a lane holds THIS box, local CPU edges park in
        # `pending` (maintenance semantics) instead of paging. A remote host's
        # load is its own — never consult local lanes for it (short-circuit).
        ci_busy = _could_be_local(config_host) and await asyncio.to_thread(
            _heavy_lanes_busy
        )
        await _react_metrics(state, label, samples, maint, ci_busy=ci_busy)
        await _react_services(state, label, samples, maint)
    # Close the CPU window ONCE per poll: every local target in this sweep then
    # measures the same interval, so a second local target cannot steal the
    # first one's window by advancing the baseline mid-sweep.
    if state.window_cpu:
        state.cpu_prev = _cpu_times_sample()


async def _react_metrics(
    state: MonitorState, label: str, samples, maint: bool, *, ci_busy: bool = False
) -> None:
    latest = {sample.metric: sample for sample in samples}
    for metric, (warn_event, ok_event, _family, _reg) in METRIC_EVENTS.items():
        sample = latest.get(metric)
        if sample is None:
            continue
        try:
            value = float(sample.value)
        except (TypeError, ValueError):
            continue
        edge = state.trackers[(label, metric)].update(value)
        level = "crit" if value >= state.crit[metric] else "warn"
        await _react(
            state,
            label,
            metric,
            edge,
            alert=(
                warn_event,
                {
                    "host": label,
                    "value": value,
                    "metric": metric,
                    "threshold": state.thresholds[metric].high,
                    "level": level,
                },
            ),
            clear=(ok_event, {"host": label, "value": value, "metric": metric}),
            value=value,
            # A held lane makes local CPU load designed, not anomalous: park the
            # edge in `pending` exactly like a maintenance window (disk, memory
            # and services keep alerting — a full disk is a problem whatever CI
            # is doing).
            maint=maint or (ci_busy and metric == "cpu_pct"),
        )


async def _react_services(state: MonitorState, label: str, samples, maint: bool) -> None:
    latest = {sample.metric: sample for sample in samples}
    for name in sorted(state.watched):
        sample = latest.get(f"service.{name}.status")
        if sample is None:
            continue  # not installed / not enumerated — absence is not an outage
        down = str(sample.value).strip().lower() in SERVICE_DOWN_STATES
        edge = state.trackers[(label, f"service:{name}")].update(1.0 if down else 0.0)
        await _react(
            state,
            label,
            f"service:{name}",
            edge,
            alert=(
                "service_down",
                {"host": label, "service": name, "state": str(sample.value)},
            ),
            clear=("service_up", {"host": label, "service": name}),
            value=None,
            maint=maint,
        )


async def _react(
    state: MonitorState,
    label: str,
    tracker_key: str,
    edge: str | None,
    *,
    alert: tuple[str, dict],
    clear: tuple[str, dict],
    value: float | None,
    maint: bool,
) -> None:
    """Settle one tracker key: maintenance bookkeeping, emit, retry, pair-clear.

    Order matters:

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
    if edge is not None:
        await _feed_threshold_trigger(state, label=label, tracker_key=tracker_key,
                                      edge=edge, value=value)

    pending = state.pending.get(key)
    if maint:
        if edge in ("alert", "clear"):
            state.pending[key] = edge
        return
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
            "host check monitor: %s reached no channel — will retry", event_type
        )
        return False
    return True


async def _feed_threshold_trigger(
    state: MonitorState,
    *,
    label: str,
    tracker_key: str,
    edge: str,
    value: float | None,
) -> None:
    """Best-effort: hand the edge to ``TriggerManager`` as a THRESHOLD event.

    Never raises and never blocks the alert path: if the manager cannot be built
    (or a trigger's action explodes), it is logged and the monitor moves on.
    """
    if state.trigger_manager is None:
        if state.triggers_unavailable:
            return
        try:
            from navig.commands.triggers import TriggerManager  # noqa: PLC0415

            state.trigger_manager = TriggerManager()
        except Exception:  # noqa: BLE001 — construction failure: log once, not per poll
            state.triggers_unavailable = True
            logger.warning("host check monitor: trigger feed unavailable", exc_info=True)
            return
    try:
        from navig.commands.triggers import TriggerEvent, TriggerType  # noqa: PLC0415

        data: dict = {"host": label, "metric": tracker_key, "state": edge}
        if value is not None:
            data["value"] = value
        event = TriggerEvent(type=TriggerType.THRESHOLD, source="host_checks", data=data)
        await asyncio.to_thread(state.trigger_manager.process_event, event)
    except Exception:  # noqa: BLE001 — a bad action must not disable the feed or the poll
        logger.warning("host check monitor: trigger feed failed", exc_info=True)


# ── the loop ───────────────────────────────────────────────────────────────────


async def run_host_check_monitor(
    config: dict | None = None, *, event_queue: object | None = None
) -> None:
    """Background task: poll hosts and emit threshold edges into the event queue."""
    cfg = config or {}
    hosts = [str(h) for h in (cfg.get("hosts") or [])] or [None]
    if any(_could_be_local(host) for host in hosts):
        try:
            import psutil  # noqa: F401, PLC0415 — local collectors need it
        except Exception:  # noqa: BLE001
            # Mirror the resources monitor: no psutil → idle, not a host_down storm.
            logger.info("host check monitor: psutil unavailable for local sampling — idle")
            return

    state = build_state(cfg, event_queue=event_queue)
    logger.info(
        "host check monitor started (targets=%s, poll=%ss)",
        [label for _host, label in state.targets],
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
                logger.warning("host check monitor: poll failed, continuing", exc_info=True)
            await asyncio.sleep(state.poll_s)
    except asyncio.CancelledError:
        logger.info("host check monitor stopped")
        raise
