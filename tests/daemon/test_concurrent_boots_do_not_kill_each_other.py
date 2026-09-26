"""Two daemons booting at once: the older wins, the younger yields, the CLI waits.

Measured 2026-09-21 20:09:41–20:10:01: `service restart` ran `schtasks /run`, the
task launched daemon A (parent: the scheduler service); A's boot sweep — a WMI
enumeration, 10–20 s here — had not written a pid file when the CLI's 10 s wait
ran out, so the CLI spawned daemon B directly; B's sweep killed A as a "stale
generation", and B — orphan-shaped — survived. Every restart on a slow machine
would end that way.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import types

import pytest

from navig.commands import service as svc
from navig.daemon import supervisor as sup

pytest.importorskip("psutil")


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "PID_FILE", tmp_path / "supervisor.pid")
    monkeypatch.setattr(sup, "STATE_FILE", tmp_path / "state.json")
    return tmp_path


# ── enumeration: supervisor-shaped, ours ─────────────────────────────────────


def _spawn(marker: str, config_dir) -> subprocess.Popen:
    env = {**os.environ, "NAVIG_CONFIG_DIR": str(config_dir)}
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", marker],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_booting_supervisor_pids_lists_our_supervisors_only(tmp_path, monkeypatch):
    mine = tmp_path / "brain"
    other = tmp_path / "other"
    mine.mkdir()
    other.mkdir()
    sup_ours = _spawn("navig.daemon.entry", mine)
    worker = _spawn("navig.daemon.telegram_worker", mine)
    sup_theirs = _spawn("navig.daemon.entry", other)
    try:
        time.sleep(0.5)
        monkeypatch.setattr(
            sup.NavigDaemon,
            "_enumerate_navig_pids",
            staticmethod(lambda: [sup_ours.pid, worker.pid, sup_theirs.pid]),
        )

        found = sup.NavigDaemon.booting_supervisor_pids(config_dir=mine)

        assert [pid for pid, _ in found] == [sup_ours.pid], found
        assert all(isinstance(t, float) and t > 0 for _, t in found)
    finally:
        for p in (sup_ours, worker, sup_theirs):
            p.kill()


# ── the younger yields; the older sweeps only what is older still ────────────


def _daemon() -> sup.NavigDaemon:
    return sup.NavigDaemon(health_port=0)


def test_a_sibling_that_started_just_before_us_makes_us_yield(isolated, monkeypatch):
    d = _daemon()
    now = 1_000_000.0
    monkeypatch.setattr(sup, "_own_create_time", lambda: now)
    monkeypatch.setattr(
        sup.NavigDaemon, "booting_supervisor_pids", staticmethod(lambda **kw: [(4242, now - 12.0)])
    )

    assert d._booting_sibling() == 4242


def test_a_sibling_that_started_after_us_is_not_ours_to_yield_to(isolated, monkeypatch):
    """It will yield to US. Two yields would leave no daemon at all."""
    d = _daemon()
    now = 1_000_000.0
    monkeypatch.setattr(sup, "_own_create_time", lambda: now)
    monkeypatch.setattr(
        sup.NavigDaemon, "booting_supervisor_pids", staticmethod(lambda **kw: [(4242, now + 3.0)])
    )

    assert d._booting_sibling() is None


def test_an_old_supervisor_is_a_stale_generation_not_a_sibling(isolated, monkeypatch):
    d = _daemon()
    now = 1_000_000.0
    monkeypatch.setattr(sup, "_own_create_time", lambda: now)
    monkeypatch.setattr(
        sup.NavigDaemon,
        "booting_supervisor_pids",
        staticmethod(lambda **kw: [(4242, now - sup.NavigDaemon.BOOT_GRACE_S - 1)]),
    )

    assert d._booting_sibling() is None


def test_run_yields_without_writing_a_pid_file_or_sweeping(isolated, monkeypatch):
    """The case that killed the task-launched daemon: we are the direct-spawn
    competitor; the older boot is the one with the living parent."""
    d = _daemon()
    swept: list = []
    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: False))
    monkeypatch.setattr(sup.NavigDaemon, "_enumerate_navig_pids", staticmethod(lambda: [4242]))
    monkeypatch.setattr(d, "_booting_sibling", lambda candidates=None: 4242)
    monkeypatch.setattr(
        sup.NavigDaemon, "_kill_orphan_daemons", staticmethod(lambda **kw: swept.append(kw) or [])
    )
    # Bounded on purpose: with the yield removed, run() would enter the real
    # supervisor loop and never return — a hang is not a red test.
    monkeypatch.setattr(d, "_supervisor_loop", _stop_immediately(d))
    monkeypatch.setattr(sup.signal, "signal", lambda *a, **kw: None)

    d.run()

    assert not (isolated / "supervisor.pid").exists(), "a yielding boot must not take the pid file"
    assert swept == [], "a yielding boot must not sweep the sibling it yields to"
    assert d._running is False


def test_the_boot_sweep_skips_anything_younger_than_us(isolated, monkeypatch):
    now = 1_000_000.0
    killed: list = []
    ages = {11: now - 100.0, 22: now + 2.0, 33: now - 0.5}  # older · younger · older
    monkeypatch.setattr(sup, "_created_before", lambda pid, instant: ages[pid] < instant)

    out = sup.NavigDaemon._kill_orphan_daemons(
        pids=[11, 22, 33],
        config_dir=isolated,
        config_dir_reader=lambda pid: isolated,
        killer=lambda pid: killed.append(pid),
        keep=set(),
        only_older_than=now,
    )

    assert out == [11, 33] and killed == [11, 33]


def test_without_an_age_the_sweep_behaves_as_before(isolated):
    killed: list = []

    out = sup.NavigDaemon._kill_orphan_daemons(
        pids=[11, 22],
        config_dir=isolated,
        config_dir_reader=lambda pid: isolated,
        killer=lambda pid: killed.append(pid),
        keep=set(),
    )

    assert out == [11, 22]


def test_run_passes_its_own_start_time_to_the_sweep(isolated, monkeypatch):
    d = _daemon()
    seen: dict = {}
    monkeypatch.setattr(sup.NavigDaemon, "is_running", staticmethod(lambda: False))
    monkeypatch.setattr(sup.NavigDaemon, "_enumerate_navig_pids", staticmethod(lambda: [9]))
    monkeypatch.setattr(d, "_booting_sibling", lambda candidates=None: None)
    monkeypatch.setattr(sup, "_own_create_time", lambda: 777.0)
    monkeypatch.setattr(
        sup.NavigDaemon, "_kill_orphan_daemons", staticmethod(lambda **kw: seen.update(kw) or [])
    )
    monkeypatch.setattr(d, "_supervisor_loop", _stop_immediately(d))
    monkeypatch.setattr(sup.signal, "signal", lambda *a, **kw: None)

    d.run()

    assert seen.get("only_older_than") == 777.0
    assert seen.get("pids") == [9], "the sweep reuses the boot's one enumeration"


def _stop_immediately(d):
    async def loop():
        d._running = False

    return loop


# ── the CLI waits for a boot that sweeps ─────────────────────────────────────


class _Daemon:
    def __init__(self, appears_after: int, booting=()):
        self.polls = 0
        self.appears_after = appears_after
        self._booting = list(booting)

    def is_running(self) -> bool:
        self.polls += 1
        return self.polls >= self.appears_after

    def booting_supervisor_pids(self):
        return self._booting

    @staticmethod
    def read_pid() -> int:
        return 1


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc.os, "name", "nt")
    monkeypatch.setattr("navig.daemon.service_manager.task_scheduler_run", lambda: (True, "ok"))
    spawned: list = []
    monkeypatch.setattr(svc, "_spawn_daemon_direct", lambda: spawned.append(True))
    return spawned


def test_the_task_path_waits_longer_than_a_boot_sweep(fast):
    """A 10 s wait was the whole bug. A daemon that sweeps needs ~20 s here."""
    d = _Daemon(appears_after=25)

    started, how = svc._launch_daemon(d)

    assert (started, how) == (True, "scheduled task") and fast == []


def test_a_visibly_booting_supervisor_is_waited_for_not_competed_with(fast):
    """Past the first wait, a supervisor of ours is still booting: wait again."""
    d = _Daemon(appears_after=svc._TASK_BOOT_WAIT_S + 10, booting=[(4242, 0.0)])

    started, how = svc._launch_daemon(d)

    assert (started, how) == (True, "scheduled task")
    assert fast == [], "spawning a competitor is exactly what killed the task launch"


def test_nothing_booting_and_nothing_up_still_falls_back(fast):
    d = _Daemon(appears_after=10_000)

    started, how = svc._launch_daemon(d)

    assert fast == [True] and how == "direct spawn"


def test_older_fakes_without_the_enumeration_still_work(fast):
    d = types.SimpleNamespace(is_running=lambda: True, read_pid=lambda: 1)

    assert svc._launch_daemon(d) == (True, "scheduled task")
