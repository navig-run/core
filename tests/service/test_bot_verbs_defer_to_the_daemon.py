"""`navig bot stop|status` under the daemon; the restart task self-registers; the
supervisor pages when it becomes orphan-shaped.

* `bot stop` force-killed the SUPERVISOR (its pattern sweep matches
  `navig.daemon.entry`) — no `_shutdown`, pid file left behind (recorded as an
  ungraceful death), and the scheduled task relaunched it within five minutes:
  the stop undid itself and cried wolf. It now stops the daemon the way the
  daemon wants to be stopped.
* An install that predates the on-demand restart task got it only from a
  re-install; `service start` — the one command every install runs — now
  registers it in its repair step.
* An orphan-shaped daemon (parent gone) was a `doctor` row you had to pull. The
  supervisor now records `daemon_orphan_shaped` once, which the config-incidents
  producer pushes — a page before the sweep, not a post-mortem after it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from navig.commands import gateway as gw_cmd
from navig.commands import service as svc
from navig.daemon import service_manager as sm
from navig.daemon import supervisor as sup

runner = CliRunner()


# ── bot stop / status ────────────────────────────────────────────────────────


def test_bot_stop_under_the_daemon_stops_the_daemon_not_the_pattern_sweep(monkeypatch):
    monkeypatch.setattr("navig.daemon.launch.supervisor_runs_the_bot", lambda: 4242)
    swept: list = []
    monkeypatch.setattr(
        "navig.daemon.single_instance.kill_other_instances",
        lambda *a, **kw: swept.append(a) or [],
    )
    stopped: list = []
    monkeypatch.setattr(svc, "service_stop", lambda: stopped.append(True))

    r = runner.invoke(gw_cmd.bot_app, ["stop"])

    assert r.exit_code == 0, r.output
    assert stopped == [True] and swept == [], "the sweep must not touch a supervised daemon"
    assert "4242" in r.output


def test_bot_stop_without_the_daemon_still_sweeps_the_standalone_processes(monkeypatch):
    monkeypatch.setattr("navig.daemon.launch.supervisor_runs_the_bot", lambda: None)
    swept: list = []
    monkeypatch.setattr(
        "navig.daemon.single_instance.kill_other_instances",
        lambda *a, **kw: swept.append(a) or [31337],
    )

    r = runner.invoke(gw_cmd.bot_app, ["stop"])

    assert r.exit_code == 0 and swept and "31337" in r.output


def test_bot_status_under_the_daemon_names_the_supervisor(monkeypatch):
    monkeypatch.setattr("navig.daemon.launch.supervisor_runs_the_bot", lambda: 4242)

    r = runner.invoke(gw_cmd.bot_app, ["status"])

    assert r.exit_code == 0 and "4242" in r.output and "navig service status" in r.output


# ── the restart task self-registers ──────────────────────────────────────────


def test_start_repair_step_registers_a_missing_restart_task(monkeypatch):
    monkeypatch.setattr(svc.os, "name", "nt")
    monkeypatch.setattr(sm, "task_scheduler_enabled_state", lambda: (True, True, "ok"))
    calls: list = []
    monkeypatch.setattr(
        sm,
        "task_scheduler_ensure_restart_task",
        lambda: calls.append(1) or (True, "Task 'X' registered (…)"),
    )
    monkeypatch.setattr(sm, "task_scheduler_enable", lambda: (True, ""))

    svc._ensure_autostart_enabled()

    assert calls == [1]


def test_start_repair_step_leaves_an_uninstalled_machine_alone(monkeypatch):
    """No daemon task → nothing to register; a start path must not nag."""
    monkeypatch.setattr(svc.os, "name", "nt")
    monkeypatch.setattr(sm, "task_scheduler_enabled_state", lambda: (None, False, "no task"))
    monkeypatch.setattr(
        sm,
        "task_scheduler_ensure_restart_task",
        lambda: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    svc._ensure_autostart_enabled()


def test_ensure_restart_task_is_a_no_op_when_present(monkeypatch):
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "task_scheduler_restart_task_installed", lambda: True)

    def boom(*a, **kw):
        raise AssertionError("schtasks must not run when the task exists")

    monkeypatch.setattr(sm.subprocess, "run", boom)

    ok, msg = sm.task_scheduler_ensure_restart_task()

    assert ok and "present" in msg


def test_ensure_restart_task_registers_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "task_scheduler_restart_task_installed", lambda: False)
    monkeypatch.setattr(sm, "daemon_dir", lambda: tmp_path / "daemon")
    monkeypatch.setattr(sm, "_navig_home", lambda: tmp_path)
    seen: list = []

    class R:
        returncode, stderr, stdout = 0, b"", b""

    monkeypatch.setattr(sm.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or R())

    ok, msg = sm.task_scheduler_ensure_restart_task()

    assert ok and "registered" in msg
    assert seen and seen[0][:2] == ["schtasks", "/create"] and sm.TASK_RESTART_NAME in seen[0]
    assert (tmp_path / "daemon" / "navig-restart-task.xml").exists()


def test_ensure_restart_task_refuses_under_pytest_by_default(monkeypatch):
    monkeypatch.delenv("NAVIG_ALLOW_TASK_MUTATION", raising=False)
    ok, msg = sm.task_scheduler_ensure_restart_task()
    assert ok is False and "refusing" in msg


# ── the supervisor pages when orphan-shaped ──────────────────────────────────


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "PID_FILE", tmp_path / "supervisor.pid")
    monkeypatch.setattr(sup, "STATE_FILE", tmp_path / "state.json")
    recorded: list[tuple[str, dict]] = []

    from navig.core import incidents

    monkeypatch.setattr(incidents, "record", lambda ev, **d: recorded.append((ev, d)))
    return recorded


def test_an_orphan_shaped_supervisor_records_the_incident_once(isolated, monkeypatch):
    d = sup.NavigDaemon(health_port=0)
    monkeypatch.setattr(
        sup.NavigDaemon,
        "parent_of",
        staticmethod(lambda pid: {"ppid": 62344, "name": None, "alive": False, "shape": "orphan"}),
    )

    d._page_if_orphan_shaped()
    d._page_if_orphan_shaped()

    assert [ev for ev, _ in isolated] == ["daemon_orphan_shaped"]
    assert isolated[0][1]["parent_pid"] == 62344 and isolated[0][1]["pid"] == os.getpid()


def test_a_service_parented_supervisor_records_nothing(isolated, monkeypatch):
    d = sup.NavigDaemon(health_port=0)
    monkeypatch.setattr(
        sup.NavigDaemon,
        "parent_of",
        staticmethod(
            lambda pid: {"ppid": 3356, "name": "svchost.exe", "alive": True, "shape": "service"}
        ),
    )

    d._page_if_orphan_shaped()

    assert isolated == []


def test_the_heartbeat_is_what_checks_for_orphaning(isolated, monkeypatch):
    """A parent can exit long after boot (the tray closes): the check rides every beat."""
    d = sup.NavigDaemon(health_port=0)
    d._write_state()
    monkeypatch.setattr(sup, "HEARTBEAT_S", 0.0)
    seen: list = []
    monkeypatch.setattr(d, "_page_if_orphan_shaped", lambda: seen.append(1))

    d._heartbeat()

    assert seen == [1]


def test_the_incident_reaches_the_push_with_the_remedy():
    from navig.core import incidents

    text = incidents.summarize(incidents.DAEMON_ORPHAN_SHAPED, {"pid": 1, "parent_pid": 2})

    assert "navig service restart" in text and "sweep" in text


def test_a_real_orphan_shaped_child_is_detected_by_the_real_classifier():
    """End to end on the OS: spawn A→B, let A exit, ask about B."""
    pytest.importorskip("psutil")
    if sys.platform != "win32":
        pytest.skip("POSIX re-parents orphans to init — the shape cannot occur")
    code = "\n".join(
        [
            "import subprocess, sys",
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],",
            "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,",
            "    creationflags=subprocess.DETACHED_PROCESS)",
            "print(p.pid, flush=True)",
        ]
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    child = int(out.stdout.strip())
    try:
        deadline = time.time() + 5
        info = sup.NavigDaemon.parent_of(child)
        while info["shape"] != "orphan" and time.time() < deadline:
            time.sleep(0.2)
            info = sup.NavigDaemon.parent_of(child)
        assert info["shape"] == "orphan", info
    finally:
        import psutil

        psutil.Process(child).kill()
