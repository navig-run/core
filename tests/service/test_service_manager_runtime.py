from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from navig.daemon import service_manager as sm

pytestmark = pytest.mark.integration


def _set_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sm, "NAVIG_HOME", tmp_path / "home")
    monkeypatch.setattr(sm, "LOG_DIR", tmp_path / "home" / "logs")
    monkeypatch.setattr(sm, "DAEMON_DIR", tmp_path / "home" / "daemon")


def test_pythonw_exe_prefers_pythonw_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    py = tmp_path / "python.exe"
    py.write_text("")
    pyw = tmp_path / "pythonw.exe"
    pyw.write_text("")

    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm.sys, "executable", str(py))
    assert sm._pythonw_exe().endswith("pythonw.exe")


def test_daemon_command_uses_python_on_non_windowless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sm, "_python_exe", lambda: "python")
    monkeypatch.setattr(sm, "_pythonw_exe", lambda: "pythonw")
    assert sm._daemon_command(windowless=True) == [
        "pythonw",
        "-m",
        "navig.daemon.entry",
    ]
    assert sm._daemon_command(windowless=False) == [
        "python",
        "-m",
        "navig.daemon.entry",
    ]


def test_detection_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm.shutil, "which", lambda name: "x" if name == "nssm" else None)
    assert sm.has_nssm() is True

    monkeypatch.setattr(sm.shutil, "which", lambda name: "x" if name == "systemctl" else None)
    assert sm.has_systemd() is True


def test_is_admin_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm.sys, "platform", "linux")
    monkeypatch.setattr(sm.os, "geteuid", lambda: 0, raising=False)
    assert sm.is_admin() is True


def test_is_admin_windows_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "ctypes", None, raising=False)
    assert sm.is_admin() is False


def test_nssm_install_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sm,
        "_daemon_command",
        lambda windowless=True: ["pythonw", "-m", "navig.daemon.entry"],
    )
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout="", stderr=b"", returncode=0)

    monkeypatch.setattr(sm.subprocess, "run", _run)
    ok, msg = sm.nssm_install(start_now=True)
    assert ok is True
    assert "installed and started" in msg
    assert calls[0][:3] == ["nssm", "install", sm.SERVICE_NAME]
    assert calls[-1][:3] == ["nssm", "start", sm.SERVICE_NAME]


def test_nssm_install_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sm,
        "_daemon_command",
        lambda windowless=True: ["pythonw", "-m", "navig.daemon.entry"],
    )

    def _run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "nssm", stderr=b"boom")

    monkeypatch.setattr(sm.subprocess, "run", _run)
    ok, msg = sm.nssm_install(start_now=False)
    assert ok is False
    assert "NSSM install failed" in msg


def test_nssm_uninstall_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sm.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="SERVICE_RUNNING", returncode=0),
    )
    ok, msg = sm.nssm_uninstall()
    assert ok is True
    assert "removed" in msg

    running, detail = sm.nssm_status()
    assert running is True
    assert "SERVICE_RUNNING" in detail


def test_task_scheduler_install_and_uninstall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_paths(monkeypatch, tmp_path)
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout="OK", stderr=b"", returncode=0)

    monkeypatch.setattr(sm.subprocess, "run", _run)

    ok, msg = sm.task_scheduler_install(start_now=True)
    assert ok is True
    assert "created and started" in msg
    assert (sm.DAEMON_DIR / "navig-task.xml").exists()
    assert calls[0][0] == "schtasks"

    ok, msg = sm.task_scheduler_uninstall()
    assert ok is True
    assert "removed" in msg


def test_task_scheduler_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sm.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="Status: Running", returncode=0),
    )
    running, detail = sm.task_scheduler_status()
    assert running is True
    assert "Running" in detail


def test_systemd_unit_path_and_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sm.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(sm, "_python_exe", lambda: "python")
    monkeypatch.setattr(sm, "NAVIG_HOME", home)
    monkeypatch.setattr(sm, "LOG_DIR", home / "logs")

    user_unit = sm._systemd_unit_path(user=True)
    assert user_unit.parent.exists()
    content = sm._systemd_unit_content(user=True)
    assert "WantedBy=default.target" in content
    assert "ExecStart=python -m navig.daemon.entry" in content


def test_systemd_install_user_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_paths(monkeypatch, tmp_path)
    unit_path = tmp_path / "user.service"
    calls: list[list[str]] = []

    monkeypatch.setattr(sm, "is_admin", lambda: False)
    monkeypatch.setattr(sm, "_systemd_unit_path", lambda user=False: unit_path)
    monkeypatch.setattr(sm, "_systemd_unit_content", lambda user=False: "[Unit]\n")
    monkeypatch.setattr(
        sm.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or SimpleNamespace(returncode=0, stderr=b""),
    )

    ok, msg = sm.systemd_install(start_now=True)
    assert ok is True
    assert "installed and started" in msg
    assert unit_path.exists()
    assert calls[0][:2] == ["systemctl", "--user"]


def test_systemd_install_system_mode_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_paths(monkeypatch, tmp_path)
    unit_path = tmp_path / "system.service"
    monkeypatch.setattr(sm, "is_admin", lambda: True)
    monkeypatch.setattr(sm, "_systemd_unit_path", lambda user=False: unit_path)
    monkeypatch.setattr(sm, "_systemd_unit_content", lambda user=False: "[Unit]\n")

    def _run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, "systemctl", stderr=b"fail")

    monkeypatch.setattr(sm.subprocess, "run", _run)
    ok, msg = sm.systemd_install(start_now=False)
    assert ok is False
    assert "systemd install failed" in msg


def test_systemd_uninstall_and_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_paths(monkeypatch, tmp_path)
    system_unit = tmp_path / "system.service"
    user_unit = tmp_path / "user.service"
    system_unit.write_text("x")

    monkeypatch.setattr(
        sm, "_systemd_unit_path", lambda user=False: user_unit if user else system_unit
    )
    monkeypatch.setattr(sm, "is_admin", lambda: True)
    monkeypatch.setattr(
        sm.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="active", stderr=b""),
    )

    ok, msg = sm.systemd_uninstall()
    assert ok is True
    assert "removed" in msg

    # inactive system + active user fallback branch
    returns = iter(
        [
            SimpleNamespace(returncode=1, stdout="inactive", stderr=""),
            SimpleNamespace(returncode=0, stdout="active", stderr=""),
            SimpleNamespace(returncode=0, stdout="user-status", stderr=""),
        ]
    )
    monkeypatch.setattr(sm.subprocess, "run", lambda *_args, **_kwargs: next(returns))
    running, detail = sm.systemd_status()
    assert running is True
    assert "user-status" in detail


def test_detect_best_method_and_install_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "has_nssm", lambda: True)
    monkeypatch.setattr(sm, "is_admin", lambda: True)
    assert sm.detect_best_method() == "nssm"

    monkeypatch.setattr(sm, "nssm_install", lambda start_now=True: (True, "ok"))
    ok, msg = sm.install(method="nssm", start_now=False)
    assert ok is True
    assert msg == "ok"

    monkeypatch.setattr(sm, "has_nssm", lambda: False)
    ok, msg = sm.install(method="nssm")
    assert ok is False
    assert "NSSM not found" in msg


def test_install_accepts_mixed_case_method(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm, "has_nssm", lambda: True)
    monkeypatch.setattr(sm, "is_admin", lambda: True)
    monkeypatch.setattr(sm, "nssm_install", lambda start_now=True: (True, "installed"))

    ok, msg = sm.install(method="NSSM", start_now=False)
    assert ok is True
    assert msg == "installed"


def test_uninstall_accepts_mixed_case_method(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm, "task_scheduler_uninstall", lambda: (True, "task removed"))

    ok, msg = sm.uninstall(method="TASK")
    assert ok is True
    assert msg == "task removed"


def test_status_accepts_mixed_case_method(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "has_nssm", lambda: True)
    monkeypatch.setattr(sm, "nssm_status", lambda: (True, "SERVICE_RUNNING"))
    monkeypatch.setattr(sm, "task_scheduler_status", lambda: (False, "not queried"))

    class DummyDaemon:
        @staticmethod
        def is_running() -> bool:
            return False

        @staticmethod
        def read_pid() -> int | None:
            return None

        @staticmethod
        def read_state() -> dict[str, object] | None:
            return None

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", DummyDaemon)

    running, detail = sm.status(method="NSSM")
    assert running is False
    assert "NSSM service: Active" in detail
    assert "Task Scheduler" not in detail

    monkeypatch.setattr(sm.sys, "platform", "linux")
    monkeypatch.setattr(sm, "has_systemd", lambda: False)
    ok, msg = sm.install(method="systemd")
    assert ok is False
    assert "systemd not found" in msg


def test_uninstall_dispatch_and_status_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm, "nssm_uninstall", lambda: (True, "nssm"))
    ok, msg = sm.uninstall(method="nssm")
    assert ok is True
    assert msg == "nssm"

    monkeypatch.setattr(sm, "task_scheduler_uninstall", lambda: (True, "task"))
    ok, msg = sm.uninstall(method="task")
    assert ok is True
    assert msg == "task"

    import navig.daemon.supervisor as supervisor

    class FakeDaemon:
        @staticmethod
        def is_running() -> bool:
            return True

        @staticmethod
        def read_pid() -> int:
            return 4321

        @staticmethod
        def read_state() -> dict:
            return {
                "children": [{"name": "telegram", "alive": True, "pid": 99, "restart_count": 1}]
            }

    monkeypatch.setattr(supervisor, "NavigDaemon", FakeDaemon)
    monkeypatch.setattr(sm.sys, "platform", "linux")
    monkeypatch.setattr(sm, "has_systemd", lambda: False)
    running, detail = sm.status()
    assert running is True
    assert "Daemon process: RUNNING" in detail
    assert "PID: 4321" in detail


def test_uninstall_auto_tries_multiple_backends_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "has_nssm", lambda: True)
    monkeypatch.setattr(sm, "nssm_uninstall", lambda: (False, "nssm missing"))
    monkeypatch.setattr(sm, "task_scheduler_uninstall", lambda: (True, "task removed"))

    ok, msg = sm.uninstall(method=None)
    assert ok is True
    assert "task: task removed" in msg


def test_status_respects_explicit_method_filter_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "has_nssm", lambda: True)
    monkeypatch.setattr(sm, "nssm_status", lambda: (True, "SERVICE_RUNNING"))
    monkeypatch.setattr(sm, "task_scheduler_status", lambda: (False, "ERROR: not found"))

    import navig.daemon.supervisor as supervisor

    class FakeDaemon:
        @staticmethod
        def is_running() -> bool:
            return False

        @staticmethod
        def read_pid() -> int | None:
            return None

        @staticmethod
        def read_state() -> dict:
            return {}

    monkeypatch.setattr(supervisor, "NavigDaemon", FakeDaemon)

    _running, detail = sm.status(method="nssm")
    assert "NSSM service: Active" in detail
    assert "Task Scheduler:" not in detail


def test_task_scheduler_status_surfaces_query_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sm.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="ERROR: missing"),
    )

    running, detail = sm.task_scheduler_status()
    assert running is False
    assert "ERROR: missing" in detail


def test_status_normalizes_backend_detail_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm.sys, "platform", "win32")
    monkeypatch.setattr(sm, "has_nssm", lambda: True)
    monkeypatch.setattr(sm, "nssm_status", lambda: (False, "\n\nSTATE   :   STOPPED\nextra"))
    monkeypatch.setattr(sm, "task_scheduler_status", lambda: (False, "\n"))

    import navig.daemon.supervisor as supervisor

    class FakeDaemon:
        @staticmethod
        def is_running() -> bool:
            return False

        @staticmethod
        def read_pid() -> int | None:
            return None

        @staticmethod
        def read_state() -> dict:
            return {}

    monkeypatch.setattr(supervisor, "NavigDaemon", FakeDaemon)

    _running, detail = sm.status()
    assert "\n\n" not in detail
    assert "NSSM service: Inactive" in detail
    assert "  Detail: STATE : STOPPED" in detail
    assert "Task Scheduler: Inactive" in detail
