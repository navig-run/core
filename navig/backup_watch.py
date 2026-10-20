"""Backup freshness: is the newest backup in a folder young enough?

A nightly backup fails silently in the usual ways — a share that did not
re-mount after a reboot, a timer that stopped, a box that is off. Each of them
looks the same from the outside: no new file. So this checks the one thing
they all break: the age of the newest matching file in the backup folder.

``check()`` is pure (folder → verdict). ``latch()`` turns verdicts into edges
using a small JSON state file, so a stale backup alerts ONCE and its recovery
announces ONCE — an hourly or daily run never repeats the same alarm.
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Verdict:
    name: str
    path: str
    ok: bool
    reason: str
    newest: str | None = None
    age_hours: float | None = None
    max_age_hours: float = 0.0


def check(folder: Path, *, name: str, max_age_hours: float, pattern: str = "*",
          now: float | None = None) -> Verdict:
    now = time.time() if now is None else now
    if not folder.is_dir():
        return Verdict(name, str(folder), False, "folder unreachable (share not mounted, or the path moved)",
                       max_age_hours=max_age_hours)
    newest: tuple[float, Path] | None = None
    try:
        for entry in folder.iterdir():
            if not entry.is_file() or not fnmatch.fnmatch(entry.name, pattern):
                continue
            mtime = entry.stat().st_mtime
            if newest is None or mtime > newest[0]:
                newest = (mtime, entry)
    except OSError as exc:
        return Verdict(name, str(folder), False, f"folder unreadable ({exc})", max_age_hours=max_age_hours)
    if newest is None:
        return Verdict(name, str(folder), False, f"no file matching {pattern!r}", max_age_hours=max_age_hours)
    age = max(0.0, (now - newest[0]) / 3600.0)
    ok = age <= max_age_hours
    reason = "fresh" if ok else f"newest backup is {age:.0f} h old (limit {max_age_hours:.0f} h)"
    return Verdict(name, str(folder), ok, reason, newest[1].name, round(age, 1), max_age_hours)


def latch(verdict: Verdict, state_file: Path) -> str | None:
    """``"stale"`` / ``"recovered"`` on an edge, ``None`` when nothing changed.

    The first ever run reports ``"stale"`` if stale (a silent failure that
    predates the watcher is exactly what it is for) and nothing if fresh.
    """
    from navig.core.json_io import atomic_write_json, load_json_for_update

    state = load_json_for_update(state_file, default={})
    was_ok = state.get(verdict.name, {}).get("ok", True)
    state[verdict.name] = {"ok": verdict.ok, "checked": int(time.time()), **asdict(verdict)}
    state_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state, state_file, indent=1)
    if was_ok and not verdict.ok:
        return "stale"
    if not was_ok and verdict.ok:
        return "recovered"
    return None
