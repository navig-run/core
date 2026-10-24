"""Account/key/device probes contributed by plugins (entry-point group ``navig.capabilities``).

A probe is a zero-argument callable returning::

    {"accounts": [{"provider": "instagram", "label": "@brand", "state": "connected",
                   "next_step": "navig social status --check"}],
     "notes": ["optional short facts"]}

``state`` is one of ``connected`` · ``expires`` · ``rejected`` · ``missing`` · ``unknown``.
Probes report *presence* only. A vault value, token or password must never appear in the
result; :func:`_sanitise` drops every field that is not in the contract, so a probe that
returns extra data cannot leak it through MCP.

Probes run in parallel with a per-probe timeout. A probe that raises or hangs yields
``{"error": …, "accounts": []}`` for that plugin and never affects the others — "could not
verify" is reported as such, never as healthy.
"""

from __future__ import annotations

import logging
import threading
import time
from importlib import metadata
from typing import Any, Callable

logger = logging.getLogger(__name__)

GROUP = "navig.capabilities"
STATES = ("connected", "expires", "rejected", "missing", "unknown")
DEFAULT_TIMEOUT_S = 2.0
CACHE_TTL_S = 60.0

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}

#: ``id`` is the plugin's own account selector (e.g. a social slot id), never a credential.
_ACCOUNT_FIELDS = ("provider", "id", "label", "state", "next_step")


def _sanitise(raw: Any) -> dict[str, Any]:
    """Keep only contract fields, truncated; anything else a probe returned is dropped."""
    if not isinstance(raw, dict):
        return {"error": "probe returned no mapping", "accounts": []}
    accounts = []
    for item in raw.get("accounts") or []:
        if not isinstance(item, dict):
            continue
        row = {k: str(item.get(k) or "")[:80] for k in _ACCOUNT_FIELDS}
        if not row["provider"]:
            continue
        if row["state"] not in STATES:
            row["state"] = "unknown"
        accounts.append(row)
    notes = [str(n)[:160] for n in (raw.get("notes") or []) if n][:5]
    return {"accounts": accounts[:40], "notes": notes}


def discover() -> dict[str, Callable[[], Any]]:
    """name -> probe callable, loaded lazily per entry point (a bad one is skipped)."""
    found: dict[str, Callable[[], Any]] = {}
    try:
        eps = metadata.entry_points(group=GROUP)
    except Exception as exc:  # noqa: BLE001
        logger.debug("capability probe discovery failed: %s", exc)
        return found
    for ep in eps:
        try:
            probe = ep.load()
        except Exception as exc:  # noqa: BLE001 — an uninstallable plugin must not break the rest
            found[ep.name] = _failing(f"probe failed to load: {exc}")
            continue
        if callable(probe):
            found[ep.name] = probe
    return found


def _failing(message: str) -> Callable[[], Any]:
    def probe() -> Any:
        raise RuntimeError(message)

    return probe


def run_probes(
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    refresh: bool = False,
    probes: dict[str, Callable[[], Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run every probe (cached for :data:`CACHE_TTL_S`); returns name -> sanitised result."""
    now = time.monotonic()
    if probes is None:
        with _LOCK:
            if not refresh and _CACHE and now - _CACHE["at"] < CACHE_TTL_S:
                return _CACHE["results"]
        probes = discover()
        cacheable = True
    else:
        cacheable = False

    results: dict[str, dict[str, Any]] = {}
    # Daemon threads, not a ThreadPoolExecutor: a pool's workers are joined at interpreter
    # exit, so one hung probe (a stuck adb, a dead network share) would hang the MCP
    # server's shutdown. A daemon thread is simply abandoned.
    slots: dict[str, dict[str, Any]] = {}
    threads: dict[str, threading.Thread] = {}
    for name, fn in probes.items():
        slot: dict[str, Any] = {}
        slots[name] = slot

        def _run(fn: Callable[[], Any] = fn, slot: dict[str, Any] = slot) -> None:
            try:
                slot["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 — reported per probe, never raised
                slot["error"] = str(exc)[:160] or type(exc).__name__

        thread = threading.Thread(target=_run, name=f"cap-probe-{name}", daemon=True)
        thread.start()
        threads[name] = thread
    deadline = time.monotonic() + timeout_s
    for name, thread in threads.items():
        thread.join(max(0.0, deadline - time.monotonic()))
        slot = slots[name]
        if thread.is_alive():
            results[name] = {"error": f"timed out after {timeout_s:g}s", "accounts": []}
        elif "error" in slot:
            results[name] = {"error": slot["error"], "accounts": []}
        else:
            results[name] = _sanitise(slot.get("value"))

    if cacheable:
        with _LOCK:
            _CACHE.update(at=now, results=results)
    return results


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
