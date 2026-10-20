"""Last-known health of each ``provider:model`` — the liveness CACHE.

Every liveness probe (`navig mode doctor`, `navig ai models --check`, the daily
heartbeat sweep) used to print its verdicts and throw them away, so the runtime
fallback had no idea which models were actually answering. When a primary
failed with nothing configured behind it, the dispatcher had nowhere to go even
though, minutes earlier, a probe had watched three other models answer fine.

This module keeps that memory: ``<config_dir>/perf/model_health.json`` maps
``"provider:model"`` → ``{status, detail, ts, source}``. Writers:

* :func:`navig.llm.liveness.probe_model` records every probe verdict.
* the dispatch paths record what they *observe* — a model that just answered a
  real request is ``live`` (throttled to one write per model per hour, so the
  hot path never pays for this on a normal call).

Readers: :mod:`navig.llm.model_fallback` (which healthy models can stand in),
``navig ai fallback`` and the deck Fallbacks page (the health dot).

Best-effort in every direction: a failed read is an empty cache, a failed write
is logged at debug. Nothing here may break the call it observes.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.llm.model_health")

FILE_NAME = "model_health.json"

#: A ``live`` verdict older than this is not trusted as "healthy" any more.
HEALTHY_MAX_AGE_S = 7 * 24 * 3600

#: An observed-live write for the same model is skipped within this window.
_LIVE_WRITE_INTERVAL_S = 3600.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"path": None, "mtime": None, "data": {}}
_last_live_write: dict[str, float] = {}


def _path() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "perf" / FILE_NAME


def _spec(provider: str, model: str) -> str:
    return f"{(provider or '').strip().lower()}:{(model or '').strip()}"


def load() -> dict[str, dict[str, Any]]:
    """The whole cache (``spec → entry``). Empty on any read failure."""
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

        # Rides out the brief lock while another process replaces the file —
        # a plain read failed there and this view came back empty.
        data = json.loads(read_text_retrying(path)) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception as exc:  # noqa: BLE001 — a corrupt cache is an empty cache
        logger.debug("model health cache unreadable: %s", exc)
        data = {}
    entries = {k: v for k, v in data.items() if isinstance(v, dict)}
    with _lock:
        _cache.update(path=str(path), mtime=mtime, data=entries)
    return dict(entries)


def _load_for_update() -> dict[str, dict[str, Any]]:
    """The cache for a read-MODIFY-write. Unlike :func:`load`, a file that EXISTS
    but cannot be read raises instead of coming back ``{}``: three processes
    (gateway, telegram worker, CLI) write this file, a read that lands on another
    one's replace fails on Windows, and saving "{} + my one verdict" over it wiped
    every other verdict — including the 401 that reroutes chats."""
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


def record(
    provider: str, model: str, status: str, detail: str = "", *, source: str = "probe"
) -> None:
    """Store one verdict. Never raises."""
    if not provider or not model or not status:
        return
    try:
        entries = _load_for_update()
        entries[_spec(provider, model)] = {
            "status": str(status),
            "detail": str(detail or "")[:200],
            "ts": time.time(),
            "source": source,
        }
        _save(entries)
    except Exception as exc:  # noqa: BLE001
        logger.debug("model health record failed: %s", exc)


def note_live(provider: str, model: str) -> None:
    """A real request just succeeded on this model. Cheap: at most one write per
    model per hour per process, and none at all when the cache already says live
    and is fresh."""
    spec = _spec(provider, model)
    now = time.time()
    if now - _last_live_write.get(spec, 0.0) < _LIVE_WRITE_INTERVAL_S:
        return
    _last_live_write[spec] = now
    try:
        cur = load().get(spec) or {}
        if cur.get("status") == "live" and now - float(cur.get("ts") or 0) < _LIVE_WRITE_INTERVAL_S:
            return
    except Exception:  # noqa: BLE001
        pass
    record(provider, model, "live", "answered a real request", source="observed")


def status_of(spec: str) -> dict[str, Any] | None:
    """The cached entry for ``provider:model`` (any ``@conn:`` suffix ignored)."""
    base = spec.split("@conn:", 1)[0]
    provider, _, model = base.partition(":")
    return load().get(_spec(provider, model))


def forget(statuses: tuple[str, ...], *, provider: str | None = None) -> list[str]:
    """Drop every verdict whose status is in ``statuses``; returns the specs
    dropped. For a credential change: an ``auth`` verdict describes the OLD key,
    and trusting it would keep routing away from a login that now works. Writes
    only when something matched. Never raises."""
    try:
        entries = _load_for_update()
        prefix = f"{provider.strip().lower()}:" if provider else ""
        gone = [k for k, v in entries.items()
                if v.get("status") in statuses and k.startswith(prefix)]
        if gone:
            for k in gone:
                entries.pop(k, None)
            _save(entries)
        return gone
    except Exception as exc:  # noqa: BLE001
        logger.debug("model health forget failed: %s", exc)
        return []


def healthy(max_age_s: float = HEALTHY_MAX_AGE_S) -> list[dict[str, Any]]:
    """Models whose last verdict is ``live`` and recent enough to trust, newest
    first, as ``{spec, provider, model, ts, source}``. Known-retired ids are
    excluded even when a stale entry still says live."""
    try:
        from navig.llm.liveness import is_retired
    except Exception:  # noqa: BLE001
        def is_retired(model, provider=None):  # type: ignore[misc]
            return False

    now = time.time()
    out: list[dict[str, Any]] = []
    for spec, entry in load().items():
        if entry.get("status") != "live":
            continue
        ts = float(entry.get("ts") or 0)
        if now - ts > max_age_s:
            continue
        provider, _, model = spec.partition(":")
        if not provider or not model or is_retired(model, provider):
            continue
        out.append({"spec": spec, "provider": provider, "model": model, "ts": ts,
                    "source": entry.get("source", "")})
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out


def reset_for_tests() -> None:
    """Forget the in-process caches (the file itself is left alone)."""
    with _lock:
        _cache.update(path=None, mtime=None, data={})
    _last_live_write.clear()
