"""Sustained rate-limit watch — a week-long 429 storm must not be silent.

The failure taxonomy deliberately classifies a 429 as ``transient`` ("the
provider is busy — not a config problem"), because one blip on a rate-limited
free tier must never become a HIGH health issue. The cost of that correctness:
a subscription capped for a WEEK looks identical to a five-minute blip.
Measured on the operator's own install (2026-10): ``anthropic:claude-opus-4-8``
was rate-limited for a week, every chat turn silently fast-retried onto the
small model, the fallback ledger stayed empty, and no notification ever fired —
the operator only found out by reading their provider's own usage page.

This module counts 429s per ``provider:model`` in a storm window so the
heartbeat can raise a ``[MEDIUM]`` issue once the failures are clearly not a
blip. Writers:

* :func:`navig.llm.liveness.probe_model` — a probe answered 429.
* :func:`navig.llm.model_fallback.note_primary_failed` — a real call did.

Reader: the heartbeat's daily beat (``[MEDIUM]`` issue), which un-suppresses an
otherwise-OK beat so it reaches the same notification path as a dead model.

Two properties, both earned from that incident:

* **A one-off success must not erase the history.** A capped account answers
  occasional small requests (measured: one real request got through mid-week),
  so clearing the counter on any success would reset it forever and the alarm
  would never fire. Entries therefore age out by the storm window instead: a
  cap that truly ends stops producing 429s and goes stale.
* **Blips must not fire the alarm.** Reporting needs BOTH a minimum number of
  events AND a minimum span between the first and the last — five 429s a
  minute apart is a retry loop, five over hours is a cap.

Best-effort in every direction: a failed read is an empty watch, a failed write
is logged at debug. Nothing here may break the call it observes.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.llm.rate_limit_watch")

FILE_NAME = "llm_rate_limit.json"

#: How long after the last 429 a storm stays "current". A standing cap keeps
#: producing events; a cap that ended does not, and its entry goes stale.
STORM_WINDOW_S = 24 * 3600.0

#: An entry needs at least this many 429s ...
MIN_EVENTS = 5
#: ... spread over at least this long, to be called sustained.
MIN_SPAN_S = 6 * 3600.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"path": None, "mtime": None, "data": {}}


def _path() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "perf" / FILE_NAME


def _spec(provider: str, model: str) -> str:
    return f"{(provider or '').strip().lower()}:{(model or '').strip()}"


def _load_cached() -> dict[str, dict[str, Any]]:
    """The whole watch (``spec → entry``). Empty on any read failure."""
    path = _path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    with _lock:
        if _cache["path"] == str(path) and _cache["mtime"] == mtime:
            return dict(_cache["data"])
    try:
        import json

        from navig.core.yaml_io import read_text_retrying

        data = json.loads(read_text_retrying(path)) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception as exc:  # noqa: BLE001 — a corrupt watch is an empty watch
        logger.debug("rate-limit watch unreadable: %s", exc)
        data = {}
    entries = {k: v for k, v in data.items() if isinstance(v, dict)}
    with _lock:
        _cache.update(path=str(path), mtime=mtime, data=entries)
    return dict(entries)


def _load_for_update() -> dict[str, dict[str, Any]]:
    """The watch for a read-MODIFY-write. Like ``model_health``, a file that
    EXISTS but cannot be read raises instead of coming back ``{}``: three
    processes (gateway, telegram worker, CLI) write this file, and saving
    ``{} + my one event`` over it would reset another process's storm."""
    from navig.core.json_io import load_json_for_update

    data = load_json_for_update(_path(), default={})
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _save(entries: dict[str, dict[str, Any]]) -> None:
    from navig.core.json_io import atomic_write_json

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(entries, path, sort_keys=True)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    with _lock:
        _cache.update(path=str(path), mtime=mtime, data=dict(entries))


def note_rate_limited(provider: str, model: str, *, now: float | None = None) -> None:
    """A 429 was just observed on ``provider:model`` — by a probe or a real
    call. Continues an open storm or starts a new one. Never raises."""
    spec = _spec(provider, model)
    if not (provider or "").strip() or not (model or "").strip() or ":" not in spec:
        return
    when = time.time() if now is None else float(now)
    try:
        entries = _load_for_update()
        prev = entries.get(spec) or {}
        if prev and when - float(prev.get("last_seen") or 0) <= STORM_WINDOW_S:
            entry = {
                "count": int(prev.get("count") or 0) + 1,
                "first_seen": float(prev.get("first_seen") or when),
                "last_seen": when,
            }
        else:
            entry = {"count": 1, "first_seen": when, "last_seen": when}
        entries[spec] = entry
        _save(entries)
    except Exception as exc:  # noqa: BLE001
        logger.debug("note_rate_limited failed: %s", exc)


def sustained(
    *,
    min_events: int | None = None,
    min_span_s: float | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Entries whose storm is current (last 429 inside the window) AND meets
    the sustained bar (≥ *min_events* over ≥ *min_span_s*). Rows are
    ``{spec, provider, model, count, first_seen, last_seen}``, newest first.
    *now* overrides the clock (tests). Never raises."""
    want_events = MIN_EVENTS if min_events is None else int(min_events)
    want_span = MIN_SPAN_S if min_span_s is None else float(min_span_s)
    moment = time.time() if now is None else float(now)
    out: list[dict[str, Any]] = []
    try:
        for spec, entry in _load_cached().items():
            count = int(entry.get("count") or 0)
            first = float(entry.get("first_seen") or 0)
            last = float(entry.get("last_seen") or 0)
            if count < want_events or (last - first) < want_span:
                continue
            if moment - last > STORM_WINDOW_S:
                continue  # the cap ended — stale, not sustained
            provider, _, model = spec.partition(":")
            out.append({"spec": spec, "provider": provider, "model": model,
                        "count": count, "first_seen": first, "last_seen": last})
    except Exception as exc:  # noqa: BLE001
        logger.debug("rate-limit sustained() failed: %s", exc)
        return []
    out.sort(key=lambda r: r["last_seen"], reverse=True)
    return out


def reset_for_tests() -> None:
    """Forget the in-process cache (the file itself is left alone)."""
    with _lock:
        _cache.update(path=None, mtime=None, data={})