"""The daemon keeps a heartbeat, so a death is DATED and a wedge is VISIBLE.

Two gaps the death incident (#1412, #1421) left open:

* Its neighbour window ran back from DETECTION — which the scheduled task can
  delay by five minutes — so a command run against an already-dead daemon was
  listed as a suspect. The supervisor now touches ``state.json`` every
  ``HEARTBEAT_S``; the mtime is "last seen alive", the death happened within one
  interval after it, and the window closes there.
* Every liveness check asked "is the pid alive?", which a supervisor stuck in a
  blocking call answers yes to while restarting nothing. The heartbeat is the one
  signal that comes from the LOOP; `doctor` reads it (see tests/cli).

Plus a bug found on the way: ``state.json["started_at"]`` was stamped on every
``_write_state`` call — which runs on every CHILD restart — so the dashboard's
"since HH:MM" moved each time the bot child bounced.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from navig.daemon import supervisor as sup


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Throwaway pid/state files and captured incidents."""
    monkeypatch.setattr(sup, "PID_FILE", tmp_path / "supervisor.pid")
    monkeypatch.setattr(sup, "STATE_FILE", tmp_path / "state.json")
    recorded: list[tuple[str, dict]] = []

    from navig.core import incidents

    monkeypatch.setattr(incidents, "record", lambda ev, **d: recorded.append((ev, d)))
    return tmp_path, recorded


def _daemon() -> sup.NavigDaemon:
    return sup.NavigDaemon(health_port=0)


def _backdate(path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def _dead_pid() -> int:
    import subprocess

    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# ── the beat itself ──────────────────────────────────────────────────────────


def test_a_heartbeat_touches_the_state_file_without_rewriting_it(isolated, monkeypatch):
    tmp, _ = isolated
    d = _daemon()
    d._write_state()
    sf = tmp / "state.json"
    before = sf.read_text(encoding="utf-8")
    _backdate(sf, 120)
    monkeypatch.setattr(sup, "HEARTBEAT_S", 0.0)

    d._heartbeat()

    assert time.time() - sf.stat().st_mtime < 5, "the beat is the mtime"
    assert sf.read_text(encoding="utf-8") == before, "a beat is a touch, not a rewrite"


def test_within_the_interval_nothing_is_touched(isolated):
    """30 s is cheap only because a beat is skipped while the last one is fresh."""
    tmp, _ = isolated
    d = _daemon()
    d._write_state()  # counts as a beat
    sf = tmp / "state.json"
    _backdate(sf, 120)

    d._heartbeat()  # real HEARTBEAT_S — the write a moment ago was the last beat

    assert time.time() - sf.stat().st_mtime > 100, "beat too soon after the last one"


def test_a_missing_state_file_is_rewritten_not_skipped(isolated, monkeypatch):
    tmp, _ = isolated
    d = _daemon()
    d._write_state()
    sf = tmp / "state.json"
    sf.unlink()
    monkeypatch.setattr(sup, "HEARTBEAT_S", 0.0)

    d._heartbeat()

    assert sf.exists()
    assert json.loads(sf.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_the_supervisor_loop_actually_beats(isolated, monkeypatch):
    """A heartbeat method nobody calls is documentation. Run the real loop."""
    d = _daemon()
    beats: list[float] = []
    real = d._heartbeat

    def spy() -> None:
        beats.append(time.monotonic())
        real()
        d._running = False  # one tick is proof; the loop sleeps 2 s per tick

    monkeypatch.setattr(d, "_heartbeat", spy)
    monkeypatch.setattr(sup, "HEARTBEAT_S", 0.0)
    d._running = True

    async def bounded() -> None:
        # The spy is what stops the loop — so a loop that never beats would run
        # forever. A hang is not a failure; bound it and turn it into one.
        try:
            await asyncio.wait_for(d._supervisor_loop(), timeout=8)
        except asyncio.TimeoutError:
            d._running = False

    asyncio.run(bounded())

    assert beats, "the loop never called _heartbeat()"
    assert sup.NavigDaemon.last_seen_alive() is not None


def test_last_seen_alive_is_the_state_file_mtime(isolated):
    tmp, _ = isolated
    d = _daemon()
    d._write_state()
    _backdate(tmp / "state.json", 300)

    seen = datetime.fromisoformat(sup.NavigDaemon.last_seen_alive())

    age = (datetime.now(timezone.utc) - seen).total_seconds()
    assert 295 < age < 310


def test_no_state_file_means_no_last_seen(isolated):
    assert sup.NavigDaemon.last_seen_alive() is None


# ── started_at is the BOOT, not the last state write ─────────────────────────


def test_started_at_survives_a_child_restart(isolated):
    tmp, _ = isolated
    d = _daemon()
    d._started_at = "2026-01-01T00:00:00+00:00"

    d._write_state()
    first = json.loads((tmp / "state.json").read_text(encoding="utf-8"))
    d._write_state()  # what the loop does after restarting a child
    second = json.loads((tmp / "state.json").read_text(encoding="utf-8"))

    assert first["started_at"] == second["started_at"] == "2026-01-01T00:00:00+00:00"
    assert second["heartbeat_s"] == sup.HEARTBEAT_S, "readers must not hardcode the interval"


# ── the death is dated, and the window closes there ──────────────────────────


def test_the_death_is_dated_by_the_last_heartbeat(isolated):
    tmp, recorded = isolated
    (tmp / "supervisor.pid").write_text(str(_dead_pid()), encoding="utf-8")
    (tmp / "state.json").write_text("{}", encoding="utf-8")
    _backdate(tmp / "state.json", 300)

    assert sup.NavigDaemon.is_running() is False

    assert len(recorded) == 1
    _, data = recorded[0]
    seen = datetime.fromisoformat(data["last_seen_alive"])
    assert 295 < (datetime.now(timezone.utc) - seen).total_seconds() < 310
    assert not (tmp / "state.json").exists(), "a reaped death cleans up like a clean stop does"


def test_a_death_without_a_heartbeat_still_records(isolated):
    """A daemon that predates heartbeats leaves no state file — the incident must
    not depend on one."""
    tmp, recorded = isolated
    (tmp / "supervisor.pid").write_text(str(_dead_pid()), encoding="utf-8")

    sup.NavigDaemon.is_running()

    assert len(recorded) == 1
    assert recorded[0][1]["last_seen_alive"] is None


class _Store:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def events_between(self, start, end, *, limit=20):
        self.calls.append((start, end))
        return []


@pytest.fixture
def audit(monkeypatch):
    store = _Store()
    import navig.store.audit as audit_mod

    monkeypatch.setattr(audit_mod, "get_audit_store", lambda: store)
    return store


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _parse(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def test_the_window_closes_one_interval_after_the_last_beat(audit):
    """Detected five minutes late: commands from those five minutes ran against a
    daemon that was already dead and must not be suspects."""
    sup.NavigDaemon._commands_before_now(_iso(20 * 60), last_seen_alive=_iso(5 * 60))

    ((start, end),) = audit.calls
    now = datetime.now(timezone.utc)
    end_age = (now - _parse(end)).total_seconds()
    assert 5 * 60 - sup.HEARTBEAT_S - 15 < end_age < 5 * 60 - sup.HEARTBEAT_S - 5, end
    assert (now - _parse(start)).total_seconds() > 15 * 60, "start still runs 15 min back"


def test_without_a_beat_the_window_ends_now(audit):
    sup.NavigDaemon._commands_before_now(_iso(20 * 60))

    ((_, end),) = audit.calls
    assert (datetime.now(timezone.utc) - _parse(end)).total_seconds() < 5


def test_a_beat_older_than_the_birth_asks_nothing(audit):
    """Inverted (a stale state file from an older daemon): an empty window must
    not be handed to the store as a reversed range."""
    out = sup.NavigDaemon._commands_before_now(_iso(60), last_seen_alive=_iso(10 * 60))

    assert out == []
    assert audit.calls == []


def test_describe_dates_the_death():
    from navig.core import incidents

    line = incidents.describe(
        {
            "event": incidents.DAEMON_DIED_UNGRACEFULLY,
            "ts": time.time(),
            "data": {"last_seen_alive": "2026-09-14T18:01:40+00:00", "previous_pid": 1},
        }
    )

    assert "last alive 18:01:40Z" in line
