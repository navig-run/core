"""`/api/daemon/restart` must not restart the daemon from INSIDE its own tree.

The route ran `navig service restart` as a child of the gateway; the gateway is a
child of the supervisor; `stop_running_daemon` kills the supervisor with
`taskkill /T` — the whole tree. The restarter died mid-stop, after
`task_scheduler_disable()` and before `task_scheduler_enable()`: daemon dead,
autostart OFF, no HTTP response. The first test below is the measurement.

On Windows the route now hands the restart to an on-demand scheduled task (its
parent is the Task Scheduler service, outside the tree) and answers at once — or
REFUSES with the remedies when the task is not registered, rather than half-doing
it. POSIX is unchanged: a SIGTERM to the gateway does not reach a new session.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time
import types

import pytest

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.daemon import service_manager as sm
from navig.gateway.routes import daemon as daemon_routes


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill /T is the Windows tree kill")
def test_measured_an_in_tree_restarter_dies_with_the_tree(tmp_path):
    """A → B → C, where C runs `taskkill /F /PID A /T` and then tries to report.
    C never reports: the tree kill takes the process that issued it. This is why
    the route must not spawn the restart from inside the gateway."""
    out = tmp_path / "survived.txt"
    c = (
        "import subprocess,sys,time,pathlib; "
        "subprocess.run(['taskkill','/F','/PID',sys.argv[1],'/T'],capture_output=True); "
        "time.sleep(1); pathlib.Path(sys.argv[2]).write_text('C survived')"
    )
    b = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]); "
        "time.sleep(20)"
    )
    a = (
        "import subprocess,sys,os,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],str(os.getpid()),sys.argv[3]]); "
        "time.sleep(20)"
    )
    proc = subprocess.Popen([sys.executable, "-c", a, b, c, str(out)])
    try:
        deadline = time.time() + 10
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        assert proc.poll() is not None, "A was never killed — the probe did not run"
        time.sleep(1.5)  # C's report would land 1 s after the kill, if C lived
        assert not out.exists(), (
            "the in-tree restarter SURVIVED the tree kill — the premise changed"
        )
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


# ── the route ────────────────────────────────────────────────────────────────


def _app() -> web.Application:
    gw = types.SimpleNamespace(
        config=types.SimpleNamespace(auth_token=None),
        policy_check=_allow,
    )
    app = web.Application()
    daemon_routes.register(app, gw)
    return app


async def _allow(_action, _actor):
    return None


@pytest.fixture
def in_tree_restart(monkeypatch):
    """Records whether the route fell back to the in-tree subprocess."""
    calls: list[str] = []

    async def fake(action, **kw):
        calls.append(action)
        return (True, "Daemon restarted", "restarted")

    monkeypatch.setattr(daemon_routes, "_run_service_action", fake)
    return calls


async def test_on_windows_the_restart_goes_to_the_task_and_answers_at_once(
    monkeypatch, in_tree_restart
):
    monkeypatch.setattr(daemon_routes.sys, "platform", "win32")
    monkeypatch.setattr(daemon_routes, "_run_restart_task", lambda: (True, "started"))

    async with TestClient(TestServer(_app())) as client:
        res = await client.post("/api/daemon/restart")
        body = await res.json()

    assert res.status == 200, body
    assert body["data"]["result_code"] == "restart_requested_via_task"
    assert in_tree_restart == [], "the in-tree restarter must never run on Windows"


async def test_on_windows_without_the_task_the_route_refuses_with_the_remedies(
    monkeypatch, in_tree_restart
):
    """Half-doing it — killing the daemon with nothing to bring it back — is the
    bug. Refuse, and say the two ways out."""
    monkeypatch.setattr(daemon_routes.sys, "platform", "win32")
    monkeypatch.setattr(
        daemon_routes,
        "_run_restart_task",
        lambda: (False, "ERROR: The system cannot find the file specified."),
    )

    async with TestClient(TestServer(_app())) as client:
        res = await client.post("/api/daemon/restart")
        body = await res.json()

    assert res.status == 409, body
    text = str(body)
    assert "navig service install" in text and "navig service restart" in text
    assert in_tree_restart == []


async def test_on_posix_the_route_restarts_as_before(monkeypatch, in_tree_restart):
    monkeypatch.setattr(daemon_routes.sys, "platform", "linux")

    def boom():
        raise AssertionError("the task path is Windows-only")

    monkeypatch.setattr(daemon_routes, "_run_restart_task", boom)

    async with TestClient(TestServer(_app())) as client:
        res = await client.post("/api/daemon/restart")

    assert res.status == 200 and in_tree_restart == ["restart"]


# ── the task ─────────────────────────────────────────────────────────────────


def test_the_restart_task_has_no_triggers_and_runs_service_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(sm, "_navig_home", lambda: tmp_path / "home")
    monkeypatch.setattr(sm, "_pythonw_exe", lambda: "C:/py/pythonw.exe")

    xml = sm._schtasks_restart_xml()

    assert "<Triggers />" in xml, "on demand only — a trigger would restart the daemon on its own"
    assert "AllowStartOnDemand>true" in xml
    assert "sys.argv=['navig', 'service', 'restart']" in xml
    assert "restart.log" in xml, "pythonw has no console — a failed restart must leave a line"
    assert sm.TASK_RESTART_NAME != sm.TASK_NAME


def test_install_registers_both_tasks_and_uninstall_removes_both(monkeypatch, tmp_path):
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    home = tmp_path / "home"
    monkeypatch.setattr(sm, "_navig_home", lambda: home)
    monkeypatch.setattr(sm, "_log_dir", lambda: home / "logs")
    monkeypatch.setattr(sm, "daemon_dir", lambda: home / "daemon")
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        return types.SimpleNamespace(stdout="OK", stderr=b"", returncode=0)

    monkeypatch.setattr(sm.subprocess, "run", _run)

    ok, _ = sm.task_scheduler_install(start_now=False)
    assert ok
    created = [c[3] for c in calls if c[:2] == ["schtasks", "/create"]]
    assert created == [sm.TASK_NAME, sm.TASK_RESTART_NAME], created
    assert (home / "daemon" / "navig-restart-task.xml").exists()

    calls.clear()
    ok, _ = sm.task_scheduler_uninstall()
    assert ok
    deleted = [c[3] for c in calls if c[:2] == ["schtasks", "/delete"]]
    assert deleted == [sm.TASK_NAME, sm.TASK_RESTART_NAME], deleted


def test_a_restart_task_that_cannot_register_does_not_fail_the_install(monkeypatch, tmp_path):
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    home = tmp_path / "home"
    monkeypatch.setattr(sm, "_navig_home", lambda: home)
    monkeypatch.setattr(sm, "_log_dir", lambda: home / "logs")
    monkeypatch.setattr(sm, "daemon_dir", lambda: home / "daemon")

    def _run(cmd, **kwargs):
        if sm.TASK_RESTART_NAME in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr=b"denied")
        return types.SimpleNamespace(stdout="OK", stderr=b"", returncode=0)

    monkeypatch.setattr(sm.subprocess, "run", _run)

    ok, msg = sm.task_scheduler_install(start_now=False)

    assert ok and "created" in msg


def test_run_restart_refuses_under_pytest_by_default(monkeypatch):
    monkeypatch.delenv("NAVIG_ALLOW_TASK_MUTATION", raising=False)
    ok, msg = sm.task_scheduler_run_restart()
    assert ok is False and "refusing" in msg


def test_run_restart_reports_what_schtasks_said(monkeypatch):
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    monkeypatch.setattr(sm.sys, "platform", "win32")
    seen: dict = {}

    class R:
        def __init__(self, rc, err=b""):
            self.returncode, self.stderr, self.stdout = rc, err, b""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return R(1, b"ERROR: The system cannot find the file specified.")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)

    ok, msg = sm.task_scheduler_run_restart()

    assert ok is False and "cannot find" in msg
    assert seen["cmd"][:2] == ["schtasks", "/run"] and sm.TASK_RESTART_NAME in seen["cmd"]


def test_status_says_when_the_restart_task_is_missing(monkeypatch):
    """An existing install predates the task: status must name the fix."""
    import navig.daemon.supervisor as supervisor

    class FakeDaemon:
        @staticmethod
        def is_running() -> bool:
            return False

        @staticmethod
        def read_pid():
            return None

        @staticmethod
        def read_state():
            return None

    monkeypatch.setattr(supervisor, "NavigDaemon", FakeDaemon)
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "has_nssm", lambda: False)
    monkeypatch.setattr(sm, "task_scheduler_status", lambda: (True, "Ready"))
    monkeypatch.setattr(
        sm,
        "task_scheduler_health",
        lambda: {"installed": True, "problems": [], "next_run": "soon"},
    )
    monkeypatch.setattr(sm, "task_scheduler_restart_task_installed", lambda: False)

    _, detail = sm.status()

    assert sm.TASK_RESTART_NAME in detail and "navig service install" in detail


# ── pythonw has no stdin ─────────────────────────────────────────────────────


def test_stdin_is_tty_is_false_not_a_traceback_under_pythonw(monkeypatch):
    """The restart task runs under pythonw, where sys.stdin is None; the
    "retry elevated?" prompt guard used to raise AttributeError there."""
    from navig.commands import service as svc

    monkeypatch.setattr(svc.sys, "stdin", None)
    assert svc._stdin_is_tty() is False

    class _Closed:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(svc.sys, "stdin", _Closed())
    assert svc._stdin_is_tty() is False


def test_service_py_has_no_bare_stdin_isatty_left():
    """Every `sys.stdin.isatty()` CALL goes through the None-safe helper (the AST,
    not the text: the helper's own docstring names the expression)."""
    import ast

    from navig.commands import service as svc

    tree = ast.parse(pathlib.Path(svc.__file__).read_text(encoding="utf-8"))
    bare = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "isatty"
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == "stdin"
    ]
    assert bare == [], f"bare sys.stdin.isatty() at lines {bare} — use _stdin_is_tty()"
