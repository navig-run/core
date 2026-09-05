from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from navig.daemon import service_manager as sm

pytestmark = pytest.mark.integration


def _set_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # service_manager resolves paths at CALL time (`_navig_home()`/`_log_dir()`/`daemon_dir()`)
    # — the module-level NAVIG_HOME/LOG_DIR/DAEMON_DIR constants these tests used to patch were
    # removed in that refactor, so the old `setattr(sm, "NAVIG_HOME", …)` raised AttributeError
    # and silently red-lined every service-install test. Patch the resolvers instead.
    home = tmp_path / "home"
    monkeypatch.setattr(sm, "_navig_home", lambda: home)
    monkeypatch.setattr(sm, "_log_dir", lambda: home / "logs")
    monkeypatch.setattr(sm, "daemon_dir", lambda: home / "daemon")


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
    # This test deliberately drives the mutating Task Scheduler path, and does it
    # safely: paths are redirected into tmp_path and subprocess is stubbed, so no
    # schtasks ever runs. The opt-in says so explicitly -- the guard's default is to
    # refuse, because a mutating call that DOES escape would change the autostart of
    # whoever is running the suite (see test_task_mutation_blocked_under_pytest.py).
    monkeypatch.setenv("NAVIG_ALLOW_TASK_MUTATION", "1")
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout="OK", stderr=b"", returncode=0)

    monkeypatch.setattr(sm.subprocess, "run", _run)

    ok, msg = sm.task_scheduler_install(start_now=True)
    assert ok is True
    assert "created and started" in msg
    assert (sm.daemon_dir() / "navig-task.xml").exists()
    assert calls[0][0] == "schtasks"

    ok, msg = sm.task_scheduler_uninstall()
    assert ok is True
    assert "removed" in msg


def test_task_scheduler_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ENABLED task reads as active.

    This used to assert against ``stdout="Status: Running"`` -- a string real
    ``schtasks`` never emits -- while the implementation substring-matched
    "running" over the verbose dump, which always contains the field label
    "Repeat: Stop If Still Running:". The fake agreed with the bug, so a DISABLED
    task reported "Active" on the operator's machine for as long as the check
    existed. The full case set lives in test_task_scheduler_honesty.py.
    """
    task_xml = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        "<Settings><Enabled>true</Enabled></Settings></Task>"
    )
    monkeypatch.setattr(
        sm.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=task_xml, stderr="", returncode=0),
    )
    running, detail = sm.task_scheduler_status()
    assert running is True
    assert "enabled" in detail.lower()


def test_systemd_unit_path_and_content(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(sm.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(sm, "_python_exe", lambda: "python")
    monkeypatch.setattr(sm, "_navig_home", lambda: home)
    monkeypatch.setattr(sm, "_log_dir", lambda: home / "logs")

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
    # status() now also consults task_scheduler_health(), which shells out to
    # PowerShell. Stub it, or this unit test reads the REAL machine's task and
    # its outcome depends on who ran it.
    monkeypatch.setattr(
        sm,
        "task_scheduler_health",
        lambda: {
            "installed": False, "last_result": None, "next_run": None,
            "can_recover": None, "problems": ["not installed"],
        },
    )

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
    # The line now states what the task can DO, not merely that it exists:
    # "Inactive" was also what a task printed whose last run had FAILED and which
    # would never fire again, which is how a dead daemon looked fine for two days.
    assert "Task Scheduler: Not installed" in detail


# ── the service must export the CANONICAL config-dir var, not just legacy NAVIG_HOME ──

def test_systemd_unit_exports_navig_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """REGRESSION: the unit set only NAVIG_HOME (legacy, honoured by memory/theme), never
    NAVIG_CONFIG_DIR (canonical — config_dir/vault/gateway.json/single-instance read it). A
    daemon installed with a custom home ran its config against the DEFAULT ~/.navig while
    memory followed the custom home: a split brain."""
    custom = tmp_path / "custom-home"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))
    content = sm._systemd_unit_content(user=True)
    assert f"Environment=NAVIG_CONFIG_DIR={custom}" in content
    # legacy var kept, at the SAME value → no divergence
    assert f"Environment=NAVIG_HOME={custom}" in content


def test_service_env_keeps_config_and_memory_on_one_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The property that matters: with the env the service writes applied, config_dir() and
    memory.navig_home() resolve to the SAME home — no split brain."""
    from navig.memory import paths as mem_paths
    from navig.platform import paths as plat_paths

    custom = str(tmp_path / "svc-home")

    # what the service now writes (NAVIG_CONFIG_DIR + NAVIG_HOME, same value)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", custom)
    monkeypatch.setenv("NAVIG_HOME", custom)
    assert plat_paths.config_dir() == mem_paths.navig_home()

    # …and the OLD behaviour (NAVIG_HOME only, no NAVIG_CONFIG_DIR) WOULD split — proving the
    # fix is load-bearing, not cosmetic.
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
    assert plat_paths.config_dir() != mem_paths.navig_home()
    assert mem_paths.navig_home() == Path(custom)


# ── the Windows Task Scheduler fallback must bake in NAVIG_CONFIG_DIR too (#302 follow-up) ──

def test_task_scheduler_xml_bakes_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A Task Scheduler <Exec> has NO env mechanism, so the task inherits only the user's
    persistent env — a shell-set NAVIG_CONFIG_DIR is lost. The launch now bakes it into a
    `pythonw -c` bootstrap; without this the Windows-fallback daemon split-brained (#302)."""
    import xml.etree.ElementTree as ET

    custom = tmp_path / "custom & home"   # '&' also exercises XML escaping
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    xml = sm._schtasks_xml()
    # well-formed even with '&' in the home (WorkingDirectory/Command/Arguments all escaped).
    # Our own generated XML — S314 (untrusted-XML) does not apply.
    root = ET.fromstring(xml.replace('<?xml version="1.0" encoding="UTF-16"?>\n', ""))  # noqa: S314
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    args = root.find(".//t:Exec/t:Arguments", ns).text
    assert "NAVIG_CONFIG_DIR" in args and "NAVIG_HOME" in args
    assert str(custom).replace("\\", "/") in args
    assert "navig.daemon.entry" in args


def test_task_bootstrap_puts_config_and_memory_on_one_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Running the baked bootstrap's env-setup makes config_dir() == memory.navig_home() —
    the property the systemd/NSSM env already guarantees, now for the task too."""
    from navig.memory import paths as mem_paths
    from navig.platform import paths as plat_paths

    custom = tmp_path / "svc-home"
    env_setup = sm._task_env_setup(custom)
    assert env_setup in sm._task_bootstrap_args(custom), "the task must run these assignments"

    # The exec writes os.environ DIRECTLY, bypassing monkeypatch, so hand it the keys
    # first. `setenv` records the pre-existing state (here: absent) and its undo deletes
    # whatever the value became; `delenv` immediately after keeps the var absent for the
    # duration, which `_task_env_setup`'s `setdefault('NAVIG_HOME', …)` needs in order to
    # take effect at all. Both halves are load-bearing.
    #
    # `delenv` alone was not enough and is why this leaked: on a var that was ABSENT it
    # records nothing to restore, so the exec's write survived teardown. NAVIG_SERVICE was
    # not registered at all. `navig/memory/paths.py` honours NAVIG_HOME as its FIRST
    # precedence rule, so every later test in the same xdist worker resolved memory paths
    # to this `tmp_path` — which pytest then deletes. Found by auditing os.environ across a
    # full run; same class as the teardowns that popped NAVIG_CONFIG_DIR (#1125).
    for _key in ("NAVIG_SERVICE", "NAVIG_CONFIG_DIR", "NAVIG_HOME"):
        monkeypatch.setenv(_key, "")
        monkeypatch.delenv(_key, raising=False)
    exec("import os; " + env_setup)  # noqa: S102 — only the os.environ[...] assignments
    assert plat_paths.config_dir() == mem_paths.navig_home() == custom


def test_task_bootstrap_gives_a_silent_boot_somewhere_to_speak(tmp_path: Path) -> None:
    """THE REGRESSION: `pythonw` has no console, so a failed boot left no evidence at all.

    The task reported `Last Result: 1` and wrote nothing — no log, no pid file, no
    traceback — while the owner's reminders silently stopped arriving. Pointing the
    boot's stdout/stderr at a file is what makes the next failure diagnosable.
    """
    args = sm._task_bootstrap_args(tmp_path / "home")

    assert "boot.log" in args
    assert "sys.stdout=sys.stderr=" in args
    # Redirection must be in place BEFORE the daemon is handed control, or a boot-time
    # traceback still goes to the void it came from.
    assert args.index("sys.stdout=sys.stderr=") < args.index("runpy.run_module")
