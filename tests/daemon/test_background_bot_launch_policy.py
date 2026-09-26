"""ONE policy for "run the bot in the background" — and no bare spawn left behind.

Five commands inlined the same detached `Popen` of `telegram_worker`. Each worker
was orphan-shaped (no living parent once the CLI exited) and wrote no pid file,
so `navig service pids` — and the sweeper that reads it — never knew it existed.
`navig.daemon.launch.start_bot_in_background` now prefers the running daemon,
then the installed service (a living parent), then the direct spawn — which
writes `worker.pid` from inside the worker so the contract covers it.
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys
import types

import pytest

from navig.daemon import launch


class _Ch:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, m):
        self.lines.append(m)

    def success(self, m):
        self.lines.append(m)

    def dim(self, m):
        self.lines.append(m)

    def warning(self, m):
        self.lines.append(m)


@pytest.fixture
def no_spawn(monkeypatch):
    spawned: list[list[str]] = []

    def fake(cmd):
        spawned.append(cmd)
        return types.SimpleNamespace(pid=777)

    monkeypatch.setattr(launch, "spawn_detached", fake)
    return spawned


# ── the policy ───────────────────────────────────────────────────────────────


def test_a_running_daemon_that_has_the_bot_means_nothing_to_start(monkeypatch, no_spawn):
    monkeypatch.setattr(launch, "supervisor_runs_the_bot", lambda: 4242)
    monkeypatch.setattr(launch, "service_is_installed", lambda: True)
    ch = _Ch()

    how = launch.start_bot_in_background(gateway=True, port=None, ch=ch)

    assert how == "daemon-running" and no_spawn == []
    assert any("4242" in line for line in ch.lines)


def test_an_installed_service_is_started_instead_of_a_detached_worker(monkeypatch, no_spawn):
    """The living parent: `service start` relaunches through the task on Windows."""
    monkeypatch.setattr(launch, "supervisor_runs_the_bot", lambda: None)
    monkeypatch.setattr(launch, "service_is_installed", lambda: True)
    started: list[dict] = []
    monkeypatch.setattr("navig.commands.service.service_start", lambda **kw: started.append(kw))

    how = launch.start_bot_in_background(gateway=False, port=None, ch=_Ch())

    assert how == "service" and started == [{"foreground": False, "logs": False}]
    assert no_spawn == []


def test_no_service_falls_back_to_the_direct_spawn_with_the_right_flags(monkeypatch, no_spawn):
    monkeypatch.setattr(launch, "supervisor_runs_the_bot", lambda: None)
    monkeypatch.setattr(launch, "service_is_installed", lambda: False)
    ch = _Ch()

    assert launch.start_bot_in_background(gateway=False, port=None, ch=ch) == "spawned"
    assert launch.start_bot_in_background(gateway=True, port=9001, ch=ch) == "spawned"

    assert no_spawn[0][-1] == "--no-gateway"
    assert no_spawn[1][-2:] == ["--port", "9001"]
    assert all(c[:3] == [sys.executable, "-m", "navig.daemon.telegram_worker"] for c in no_spawn)
    assert any("777" in line for line in ch.lines), "the pid is the operator's handle on it"
    assert any("navig service install" in line for line in ch.lines), "say how to get a parent"


def test_supervisor_runs_the_bot_respects_the_daemon_config(monkeypatch):
    from navig.daemon import entry, supervisor

    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(lambda: True))
    monkeypatch.setattr(supervisor.NavigDaemon, "read_pid", staticmethod(lambda: 99))
    monkeypatch.setattr(entry, "_load_config", lambda: {"telegram_bot": False})
    assert launch.supervisor_runs_the_bot() is None

    monkeypatch.setattr(entry, "_load_config", lambda: {"telegram_bot": True})
    assert launch.supervisor_runs_the_bot() == 99

    monkeypatch.setattr(supervisor.NavigDaemon, "is_running", staticmethod(lambda: False))
    assert launch.supervisor_runs_the_bot() is None


def test_service_is_installed_reads_the_task_on_windows(monkeypatch):
    from navig.daemon import service_manager as sm

    monkeypatch.setattr(launch.sys, "platform", "win32")
    monkeypatch.setattr(sm, "task_scheduler_enabled_state", lambda: (True, True, ""))
    assert launch.service_is_installed() is True
    monkeypatch.setattr(sm, "task_scheduler_enabled_state", lambda: (None, False, "no task"))
    assert launch.service_is_installed() is False
    monkeypatch.setattr(sm, "task_scheduler_enabled_state", lambda: (None, None, "unreadable"))
    assert launch.service_is_installed() is False, "unknown must not stall a launch on a probe"


# ── gateway start --background ───────────────────────────────────────────────


def test_gateway_background_spawns_a_detached_gateway_start_with_its_flags(monkeypatch, no_spawn):
    """This flag used to print "not yet implemented" and run in the FOREGROUND."""
    monkeypatch.setattr(launch, "supervisor_runs_the_gateway", lambda: None)
    monkeypatch.setattr(launch, "service_is_installed", lambda: False)

    how = launch.start_gateway_in_background(port=9100, host="0.0.0.0", ch=_Ch())

    assert how == "spawned"
    assert no_spawn == [
        [sys.executable, "-m", "navig", "gateway", "start", "--port", "9100", "--host", "0.0.0.0"]
    ]


def test_gateway_background_under_the_daemon_starts_nothing(monkeypatch, no_spawn):
    monkeypatch.setattr(launch, "supervisor_runs_the_gateway", lambda: 4242)

    assert launch.start_gateway_in_background(port=None, host=None, ch=_Ch()) == "daemon-running"
    assert no_spawn == []


def test_gateway_background_uses_the_service_only_when_it_runs_a_gateway(monkeypatch, no_spawn):
    """A service configured bot-only would not give the operator a gateway."""
    from navig.daemon import entry

    monkeypatch.setattr(launch, "supervisor_runs_the_gateway", lambda: None)
    monkeypatch.setattr(launch, "service_is_installed", lambda: True)
    started: list = []
    monkeypatch.setattr("navig.commands.service.service_start", lambda **kw: started.append(kw))

    monkeypatch.setattr(entry, "_load_config", lambda: {"gateway": False})
    assert launch.start_gateway_in_background(port=None, host=None, ch=_Ch()) == "spawned"
    assert started == [] and len(no_spawn) == 1

    monkeypatch.setattr(entry, "_load_config", lambda: {"gateway": True})
    assert launch.start_gateway_in_background(port=None, host=None, ch=_Ch()) == "service"
    assert started == [{"foreground": False, "logs": False}]


def test_gateway_stop_under_the_daemon_refuses_and_says_why(monkeypatch):
    """The supervisor restarts a stopped child in seconds: "stopped" was a lie."""
    from typer.testing import CliRunner

    from navig.commands import gateway as gw_cmd

    monkeypatch.setattr("navig.daemon.launch.supervisor_runs_the_gateway", lambda: 4242)

    r = CliRunner().invoke(gw_cmd.gateway_app, ["stop"])

    assert r.exit_code == 1, r.output
    assert "navig service stop" in r.output and "navig service restart" in r.output


# ── the worker's pid file ────────────────────────────────────────────────────


def test_the_worker_writes_and_removes_only_its_own_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(launch.paths, "config_dir", lambda: tmp_path)

    launch.write_worker_pid()
    assert (tmp_path / "worker.pid").read_text(encoding="utf-8") == str(os.getpid())

    (tmp_path / "worker.pid").write_text("123456", encoding="utf-8")  # a newer worker took it
    launch.remove_worker_pid()
    assert (tmp_path / "worker.pid").exists(), "never remove a file that names someone else"

    (tmp_path / "worker.pid").write_text(str(os.getpid()), encoding="utf-8")
    launch.remove_worker_pid()
    assert not (tmp_path / "worker.pid").exists()


def test_a_standalone_worker_is_a_root_for_service_pids(tmp_path, monkeypatch):
    pytest.importorskip("psutil")
    from navig.daemon import supervisor as sup

    monkeypatch.setattr(sup, "PID_FILE", tmp_path / "supervisor.pid")
    monkeypatch.setattr(sup.paths, "config_dir", lambda: tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        (tmp_path / "worker.pid").write_text(str(proc.pid), encoding="utf-8")

        trees = sup.NavigDaemon.owned_process_trees()

        assert [t["role"] for t in trees] == ["worker"] and trees[0]["root"] == proc.pid
    finally:
        proc.kill()


def test_the_worker_entry_writes_the_pid_around_its_run():
    """The write must wrap `_run` (and the removal must be in a `finally`) — the
    AST, not the text, so a refactor that moves it out of the try is caught."""
    from navig.daemon import telegram_worker

    tree = ast.parse(pathlib.Path(telegram_worker.__file__).read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [
        n.func.id
        for n in ast.walk(main)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert "write_worker_pid" in calls
    tries = [n for n in ast.walk(main) if isinstance(n, ast.Try)]
    assert any(
        any(
            isinstance(c, ast.Expr)
            and isinstance(c.value, ast.Call)
            and getattr(c.value.func, "id", "") == "remove_worker_pid"
            for c in t.finalbody
        )
        for t in tries
    ), "remove_worker_pid must run in a finally"


# ── no bare spawn left behind ────────────────────────────────────────────────


def test_no_command_spawns_the_worker_outside_the_policy():
    """A FUNCTION under navig/commands that both names `navig.daemon.telegram_worker`
    and calls `subprocess.Popen` is a new orphan-shaped worker outside the contract.
    Function granularity on purpose: the five old sites built `cmd = [...]` and then
    `Popen(cmd, ...)`, so the call itself never named the worker."""
    import navig.commands

    root = pathlib.Path(navig.commands.__file__).parent
    offenders: list[str] = []
    scanned = 0
    for f in root.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        if "navig.daemon.telegram_worker" not in src:
            continue
        scanned += 1
        for fn in [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)]:
            seg = ast.get_source_segment(src, fn) or ""
            if "navig.daemon.telegram_worker" not in seg:
                continue
            pops = [
                n
                for n in ast.walk(fn)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "Popen"
            ]
            if pops:
                offenders.append(f"{f.name}:{fn.name}:{pops[0].lineno}")
    assert scanned >= 3, "the scan must have seen the command modules that name the worker"
    assert offenders == [], f"detached worker spawns outside navig.daemon.launch: {offenders}"
