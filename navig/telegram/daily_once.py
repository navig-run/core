"""Per-window "at most once" claims for time-based notices.

A scheduled notice can be evaluated by more than one path: the notifier's own
engagement tick, the proactive engine, a restarted supervisor, or a daemon and a
``telegram_worker`` process holding the same job. Cooldowns kept in process
memory cannot stop the second one — and the operator measured the cost: two
"NAVIG — Приветствие" cards four minutes apart (07:13 and 07:17) on the same
morning.

:func:`daily_once` is the durable claim those senders consult BEFORE they send:

* the window is the **local calendar day** by default, or a rolling
  ``window_hours`` when a notice is paced by the hour rather than the date
  (``outpost:<host>``);
* the claim lives in one small JSON file under :func:`paths.data_dir`, written
  atomically, so a restart, a second process, or a duplicate scheduler path all
  read the same answer;
* a failed or unreadable read SUPPRESSES the send rather than repeating it —
  the purpose of this file is "never twice in a day", so when the answer is
  unknown the quiet choice is the correct one;
* a corrupt file is quarantined (``*.corrupt``) by :mod:`navig.core.json_io`
  and replaced with a clean one, so a torn write cannot wedge the sender
  forever. Losing the ledger can cost one repeat, never a permanent silence.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from navig.core.json_io import JsonReadError, atomic_write_json, load_json_for_update

logger = logging.getLogger(__name__)

#: Entries older than this are noise; the ledger stays a few hundred bytes.
RETENTION_DAYS = 90

#: Hard cap, so a pathological caller with a unique key per second cannot grow
#: the file without bound. Two entries per day is the real-world shape.
MAX_ENTRIES = 500

_STATE_VERSION = 1
_DEFAULT_STATE: dict[str, Any] = {"version": _STATE_VERSION, "entries": {}}


def state_path() -> Path:
    """Where the claims live — resolved at CALL time so ``NAVIG_DATA_DIR`` applies."""
    from navig.platform import paths

    return paths.data_dir() / "telegram" / "daily_once.json"


# ── cross-process lock (best effort) ────────────────────────────────────────


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Serialise a claim's load-modify-save across processes.

    Two senders racing on the same key is exactly the failure this module exists
    to prevent, and an atomic write alone cannot serialise a read-then-write. The
    lock degrades to unlocked behaviour on a read-only or locked directory: the
    worst case is then the pre-existing race, never a failed send.
    """
    handle = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path.with_suffix(path.suffix + ".lock"), "a+b")  # noqa: SIM115
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # ~10 s, then OSError
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError as exc:
        logger.debug("daily_once lock unavailable, claiming unlocked: %s", exc)
        if handle is not None:
            handle.close()
            handle = None
    try:
        yield
    finally:
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


# ── the claim ───────────────────────────────────────────────────────────────


def daily_once(
    key: str,
    now: datetime | None = None,
    *,
    window_hours: float | None = None,
) -> bool:
    """True the first time *key* is claimed in its window; False when suppressed.

    ``key`` is a stable name such as ``"briefing:morning"``, ``"tip:daily"`` or
    ``"outpost:lighthouse"``. ``window_hours`` switches the window from the local
    calendar day to a rolling period (``6`` = at most once per six hours).

    Claiming happens BEFORE the caller sends. That ordering is deliberate: two
    processes evaluating the same notice at the same instant cannot both win, and
    not sending is recoverable while a duplicate cannot be taken back.
    """
    name = str(key or "").strip()
    if not name:
        raise ValueError("daily_once needs a non-empty key")
    moment = _local(now)
    start = _window_start(moment, window_hours)
    path = state_path()

    with _locked(path):
        try:
            state = _load(path)
        except JsonReadError as exc:
            # The file exists but is transiently unreadable. Claiming would risk a
            # duplicate; refuse the send and say why.
            logger.warning("daily_once: %s unreadable — suppressing %s: %s", path, name, exc)
            return False
        entries = state["entries"]
        previous = _parse(entries.get(name))
        if previous is not None and previous >= start:
            logger.debug("daily_once: %s already claimed for this window — suppressed", name)
            return False
        entries[name] = moment.isoformat()
        _prune(entries, moment)
        try:
            atomic_write_json(state, path, indent=1)
        except OSError as exc:  # a claim that cannot persist still permits THIS send
            logger.warning("daily_once: could not persist %s for %s: %s", name, path, exc)
        return True


def last_claim(key: str) -> datetime | None:
    """When *key* was last claimed, or None. Read-only and never raises."""
    try:
        state = _load(state_path())
    except JsonReadError:
        return None
    return _parse(state["entries"].get(str(key or "").strip()))


def clear(key: str) -> bool:
    """Forget one claim (operator reset / test seam). True when something was removed."""
    path = state_path()
    with _locked(path):
        try:
            state = _load(path)
        except JsonReadError:
            return False
        if state["entries"].pop(str(key or "").strip(), None) is None:
            return False
        try:
            atomic_write_json(state, path, indent=1)
        except OSError:
            return False
    return True


# ── internals ───────────────────────────────────────────────────────────────


def _load(path: Path) -> dict[str, Any]:
    """The ledger as ``{"version": 1, "entries": {key: iso}}``.

    ``load_json_for_update`` gives the safety this file needs: a transient lock
    raises (and the caller suppresses), while genuinely corrupt bytes are
    quarantined beside the store and replaced by a clean ledger.
    """
    data = load_json_for_update(path, default=dict(_DEFAULT_STATE))
    if not isinstance(data, dict):
        return {"version": _STATE_VERSION, "entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    data["version"] = _STATE_VERSION
    data["entries"] = entries
    return data


def _local(now: datetime | None) -> datetime:
    """Local wall-clock time, naive — the form every stored claim is written in.

    An aware *now* is converted to this machine's zone and stripped, so an aware
    caller and a naive stored value can still be compared. Mixing the two raised
    ``TypeError: can't compare offset-naive and offset-aware datetimes``.
    """
    moment = now or datetime.now()
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


def _window_start(now: datetime, window_hours: float | None) -> datetime:
    if window_hours is None:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        hours = max(0.0, float(window_hours))
    except (TypeError, ValueError):
        hours = 0.0
    return now - timedelta(hours=hours)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _local(parsed)


def _prune(entries: dict[str, Any], now: datetime) -> None:
    cutoff = now - timedelta(days=RETENTION_DAYS)
    stale = [key for key, value in entries.items()
             if (parsed := _parse(value)) is None or parsed < cutoff]
    for key in stale:
        entries.pop(key, None)
    if len(entries) <= MAX_ENTRIES:
        return
    newest = sorted(entries.items(), key=lambda item: str(item[1]), reverse=True)
    entries.clear()
    entries.update(dict(newest[:MAX_ENTRIES]))


__all__ = [
    "MAX_ENTRIES",
    "RETENTION_DAYS",
    "clear",
    "daily_once",
    "last_claim",
    "state_path",
]
