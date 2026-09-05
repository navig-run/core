from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands.service import service_app

pytestmark = pytest.mark.integration

runner = CliRunner()


def _patch_daemon_start_failure(monkeypatch):
    class FakeDaemon:
        @staticmethod
        def is_running():
            return False

        @staticmethod
        def read_pid():
            return None

        @staticmethod
        def stop_running_daemon():
            return True

    class DummyPopen:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)
    monkeypatch.setattr("navig.daemon.service_manager._pythonw_exe", lambda: "python")
    monkeypatch.setattr("subprocess.Popen", DummyPopen)
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)
    # Ensure the stop-intent flag never blocks the test regardless of on-disk state.
    monkeypatch.setattr("navig.daemon.service_manager.stop_flag_is_set", lambda: False)


def test_service_restart_failure_returns_exit_code_1(monkeypatch):
    _patch_daemon_start_failure(monkeypatch)

    result = runner.invoke(service_app, ["restart"])

    assert result.exit_code == 1


def test_service_start_failure_returns_exit_code_1(monkeypatch):
    _patch_daemon_start_failure(monkeypatch)

    result = runner.invoke(service_app, ["start"])

    assert result.exit_code == 1


def test_service_restart_stop_failure_returns_exit_code_1(monkeypatch):
    class FakeDaemon:
        @staticmethod
        def is_running():
            return True

        @staticmethod
        def read_pid():
            return 12345

        @staticmethod
        def stop_running_daemon():
            return False

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)

    result = runner.invoke(service_app, ["restart"])

    assert result.exit_code == 1


def test_service_uninstall_uses_auto_backend_when_method_omitted(monkeypatch):
    class FakeDaemon:
        @staticmethod
        def is_running():
            return False

        @staticmethod
        def read_pid():
            return None

        @staticmethod
        def stop_running_daemon():
            return True

    captured: dict[str, object] = {}

    def _fake_uninstall(method=None):
        captured["method"] = method
        return True, "ok"

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)
    monkeypatch.setattr("navig.daemon.service_manager.uninstall", _fake_uninstall)
    monkeypatch.setattr(
        "navig.daemon.service_manager.detect_best_method",
        lambda: (_ for _ in ()).throw(AssertionError("detect_best_method should not be called")),
    )

    result = runner.invoke(service_app, ["uninstall"])

    assert result.exit_code == 0
    assert captured["method"] is None


def test_service_uninstall_stop_failure_returns_exit_code_1(monkeypatch):
    class FakeDaemon:
        @staticmethod
        def is_running():
            return True

        @staticmethod
        def read_pid():
            return 999

        @staticmethod
        def stop_running_daemon():
            return False

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)

    result = runner.invoke(service_app, ["uninstall"])

    assert result.exit_code == 1


def test_service_uninstall_admin_relaunches_when_not_elevated(monkeypatch):
    from navig.commands import service

    calls: dict[str, object] = {}

    def _fake_relaunch(sub):
        calls["sub"] = sub
        return 0

    monkeypatch.setattr(service, "_is_elevated", lambda: False)
    monkeypatch.setattr(service, "_relaunch_elevated", _fake_relaunch)

    result = runner.invoke(service_app, ["uninstall", "--admin"])

    assert result.exit_code == 0
    assert calls["sub"] == ["service", "uninstall"]


def test_service_uninstall_admin_forwards_method(monkeypatch):
    # The elevated child must remove the SAME backend the operator asked for.
    from navig.commands import service

    calls: dict[str, object] = {}

    def _fake_relaunch(sub):
        calls["sub"] = sub
        return 0

    monkeypatch.setattr(service, "_is_elevated", lambda: False)
    monkeypatch.setattr(service, "_relaunch_elevated", _fake_relaunch)

    result = runner.invoke(service_app, ["uninstall", "--admin", "--method", "nssm"])

    assert result.exit_code == 0
    assert calls["sub"] == ["service", "uninstall", "--method", "nssm"]


def _patch_status_running(monkeypatch, *, pid=4242):
    from navig.commands import service

    class FakeDaemon:
        @staticmethod
        def is_running():
            return True

        @staticmethod
        def read_pid():
            return pid

        @staticmethod
        def read_state():
            return {"children": []}

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)
    monkeypatch.setattr(
        "navig.daemon.service_manager.status", lambda method=None: (True, "Daemon process: RUNNING")
    )
    monkeypatch.setattr(
        service, "reachability_summary",
        lambda: {"gateway_url": "http://127.0.0.1:8789", "mode": "local-only",
                 "reach_url": "http://127.0.0.1:8789", "deck_url": "http://127.0.0.1:8789",
                 "gateway_port": "8789"},
    )
    return service


def test_service_status_warns_when_daemon_elevated_and_shell_is_not(monkeypatch):
    service = _patch_status_running(monkeypatch)
    monkeypatch.setattr(service, "_process_is_elevated", lambda pid: True)
    monkeypatch.setattr(service, "_is_elevated", lambda: False)

    result = runner.invoke(service_app, ["status"])
    assert result.exit_code == 0
    assert "elevated" in result.output.lower() and "--admin" in result.output


def test_service_status_no_warning_when_shell_also_elevated(monkeypatch):
    service = _patch_status_running(monkeypatch)
    monkeypatch.setattr(service, "_process_is_elevated", lambda pid: True)
    monkeypatch.setattr(service, "_is_elevated", lambda: True)  # can already stop it → no warning

    result = runner.invoke(service_app, ["status"])
    assert result.exit_code == 0
    assert "--admin" not in result.output


def test_service_status_json_includes_service_manager_detail(monkeypatch):
    class FakeDaemon:
        @staticmethod
        def is_running():
            return False

        @staticmethod
        def read_pid():
            return 321

        @staticmethod
        def stop_running_daemon():
            return True

        @staticmethod
        def read_state():
            return {"children": [{"name": "gateway", "alive": True}]}

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)
    monkeypatch.setattr(
        "navig.daemon.service_manager.status",
        lambda method=None: (True, "Daemon process: RUNNING"),
    )

    result = runner.invoke(service_app, ["status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["running"] is True
    assert payload["pid"] == 321
    assert payload["children"] == [{"name": "gateway", "alive": True}]
    assert payload["detail"] == "Daemon process: RUNNING"


def test_service_config_show_malformed_json_returns_exit_code_1(monkeypatch, tmp_path):
    bad = tmp_path / "bad-config.json"
    bad.write_text("{not-json", encoding="utf-8")

    monkeypatch.setattr("navig.daemon.entry.save_default_config", lambda: bad)

    result = runner.invoke(service_app, ["config", "--show"])

    assert result.exit_code == 1
    assert "Failed to read daemon config" in result.stdout


def test_service_logs_rejects_zero_lines():
    result = runner.invoke(service_app, ["logs", "--lines", "0"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Regression tests added for Windows service reliability hardening
# ---------------------------------------------------------------------------


def test_service_start_polls_for_pid_on_slow_start(monkeypatch):
    """service start must succeed even if the daemon PID file appears after
    the first check — i.e., it polls rather than doing a single fixed wait."""
    call_count = {"n": 0}

    class SlowDaemon:
        @staticmethod
        def is_running():
            call_count["n"] += 1
            # Simulate: not running for first 3 polls, then appears.
            return call_count["n"] >= 3

        @staticmethod
        def read_pid():
            return 99999

        @staticmethod
        def stop_running_daemon():
            return True

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", SlowDaemon)
    monkeypatch.setattr("navig.daemon.service_manager._pythonw_exe", lambda: "python")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: None)
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr("navig.daemon.service_manager.stop_flag_is_set", lambda: False)
    monkeypatch.setattr("navig.daemon.service_manager.task_scheduler_enable", lambda: None)

    result = runner.invoke(service_app, ["start"])

    assert result.exit_code == 0, result.output
    assert "99999" in result.output
    # Must have polled more than once
    assert call_count["n"] >= 3


def test_service_start_interactive_stop_flag_override_clears_both_guards(monkeypatch):
    """An interactive user typing `navig service start` must clear both the
    stop-intent flag and the watchdog deadline and proceed to start.

    Note: uses direct function call instead of CliRunner because CliRunner
    replaces sys.stdin with a non-interactive stream, defeating isatty()."""
    import sys as _sys

    cleared: dict[str, bool] = {"flag": False, "deadline": False}

    class FakeDaemon:
        _call = 0

        @staticmethod
        def is_running():
            # First call: not running (allows start to proceed past the early-exit
            # guard). Second call: running (the daemon has started).
            FakeDaemon._call += 1
            return FakeDaemon._call > 1

        @staticmethod
        def read_pid():
            return 12345

        @staticmethod
        def stop_running_daemon():
            return True

    # Patch sys.stdin so isatty() returns True (simulates a real terminal).
    monkeypatch.setattr(_sys, "stdin", type("FakeStdin", (), {"isatty": lambda self: True})())
    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)
    monkeypatch.setattr("navig.daemon.service_manager._pythonw_exe", lambda: "python")
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: None)
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr("navig.daemon.service_manager.stop_flag_is_set", lambda: True)
    monkeypatch.setattr(
        "navig.daemon.service_manager.clear_stop_flag",
        lambda: cleared.__setitem__("flag", True),
    )
    monkeypatch.setattr(
        "navig.daemon.service_manager.clear_watchdog_deadline",
        lambda: cleared.__setitem__("deadline", True),
    )
    monkeypatch.setattr("navig.daemon.service_manager.task_scheduler_enable", lambda: None)
    # `service_start` finishes by tailing the logs, and that is a deliberate
    # `while True: time.sleep(0.5)` broken only by KeyboardInterrupt — correct for a
    # real `tail -f`, fatal for a test. Stubbing time.sleep (above) does not help: it
    # just turns the wait into a 100%-CPU spin. Everything asserted here happens
    # BEFORE the tail, so stub the tail itself and let the call return.
    monkeypatch.setattr("navig.commands.service._tail_service_logs", lambda *a, **k: None)

    from navig.commands.service import service_start

    service_start(foreground=False)  # call directly — CliRunner would override sys.stdin

    assert cleared["flag"] is True, "clear_stop_flag() must be called on interactive override"
    assert cleared["deadline"] is True, "clear_watchdog_deadline() must be called on interactive override"


def test_spawn_stop_watchdog_uses_pythonw_not_sys_executable(monkeypatch, tmp_path):
    """_spawn_stop_watchdog must use _pythonw_exe() (pythonw.exe on Windows),
    NOT sys.executable (python.exe), to avoid creating a visible console window."""
    import sys

    from navig.commands.service import _spawn_stop_watchdog

    captured: dict[str, object] = {}

    def _fake_popen(cmd, **kwargs):
        captured["exe"] = cmd[0]

    # `daemon_dir` is a FUNCTION, not a DAEMON_DIR constant — the constant this used
    # to patch has never existed, so the test died on this line before asserting
    # anything. `_spawn_stop_watchdog` lazily imports both names from
    # service_manager, so patching them there is what actually takes effect.
    monkeypatch.setattr("navig.daemon.service_manager.daemon_dir", lambda: tmp_path)
    monkeypatch.setattr("navig.daemon.service_manager._pythonw_exe", lambda: "/fake/pythonw")
    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    # Provide a fake deadline file so the watchdog script can be written
    (tmp_path / "stop_watchdog_deadline").write_text("99999999999.0", encoding="utf-8")

    _spawn_stop_watchdog(duration=1)

    assert "exe" in captured, "Popen must have been called"
    assert captured["exe"] == "/fake/pythonw", (
        f"Watchdog must use _pythonw_exe(), got: {captured['exe']!r}"
    )
    # Must NOT use sys.executable directly
    assert captured["exe"] != sys.executable


def test_service_install_handles_malformed_existing_config(monkeypatch, tmp_path):
    bad_config = tmp_path / "daemon-config.json"
    bad_config.write_text("{bad-json", encoding="utf-8")

    monkeypatch.setattr("navig.daemon.entry.save_default_config", lambda: bad_config)
    monkeypatch.setattr("navig.daemon.service_manager.detect_best_method", lambda: "task")

    called: dict[str, object] = {}

    def _fake_install(method=None, start_now=True):
        called["method"] = method
        called["start_now"] = start_now
        return True, "ok"

    monkeypatch.setattr("navig.daemon.service_manager.install", _fake_install)

    result = runner.invoke(
        service_app,
        ["install", "--no-start", "--gateway", "--scheduler", "--health-port", "123"],
    )

    assert result.exit_code == 0
    assert called["method"] == "task"
    assert called["start_now"] is False

    cfg = json.loads(bad_config.read_text(encoding="utf-8"))
    assert cfg["telegram_bot"] is True
    assert cfg["gateway"] is True
    assert cfg["scheduler"] is True
    assert cfg["health_port"] == 123


def test_stop_failure_message_prefers_recorded_reason(monkeypatch):
    from navig.commands import service

    class FakeDaemon:
        _last_stop_error = ("access denied — the daemon (pid 999) is running elevated "
                            "(Administrator) but this terminal is not.")

        @staticmethod
        def read_pid():
            return 999

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)
    msg = service._stop_failure_message()
    assert "elevated" in msg.lower() and "999" in msg


def test_stop_failure_message_falls_back_to_kill_hint(monkeypatch):
    from navig.commands import service

    class FakeDaemon:
        _last_stop_error = None

        @staticmethod
        def read_pid():
            return 4242

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)
    msg = service._stop_failure_message()
    assert "4242" in msg  # a concrete manual force-kill hint


def test_service_restart_admin_relaunches_when_not_elevated(monkeypatch):
    from navig.commands import service

    calls: dict[str, object] = {}

    def _fake_relaunch(sub):
        calls["sub"] = sub
        return 0

    monkeypatch.setattr(service, "_is_elevated", lambda: False)
    monkeypatch.setattr(service, "_relaunch_elevated", _fake_relaunch)

    result = runner.invoke(service_app, ["restart", "--admin"])

    assert result.exit_code == 0
    assert calls["sub"] == ["service", "restart"]  # handed off, before touching the daemon


def test_service_stop_admin_relaunches_when_not_elevated(monkeypatch):
    from navig.commands import service

    calls: dict[str, object] = {}

    def _fake_relaunch(sub):
        calls["sub"] = sub
        return 0

    monkeypatch.setattr(service, "_is_elevated", lambda: False)
    monkeypatch.setattr(service, "_relaunch_elevated", _fake_relaunch)

    result = runner.invoke(service_app, ["stop", "--admin"])

    assert result.exit_code == 0
    assert calls["sub"] == ["service", "stop"]


def test_service_restart_admin_does_not_relaunch_when_already_elevated(monkeypatch):
    # Already elevated → skip the UAC relaunch entirely and run the normal flow.
    from navig.commands import service

    _patch_daemon_start_failure(monkeypatch)  # is_running False + Popen/sleep stubbed
    monkeypatch.setattr(service, "_is_elevated", lambda: True)
    monkeypatch.setattr(
        service, "_relaunch_elevated",
        lambda *a: (_ for _ in ()).throw(AssertionError("must not relaunch when elevated")),
    )
    monkeypatch.setattr("navig.daemon.service_manager.task_scheduler_disable", lambda: None)
    monkeypatch.setattr("navig.daemon.service_manager.task_scheduler_enable", lambda: None)

    result = runner.invoke(service_app, ["restart", "--admin"])

    # No relaunch happened (no AssertionError); the normal start path ran and failed
    # in the stubbed environment.
    assert result.exit_code == 1


def test_service_restart_direct_call_ignores_optioninfo_default(monkeypatch):
    # `navig gateway restart` calls service_restart() DIRECTLY. An unpassed
    # typer.Option arrives as a truthy OptionInfo, not False — the coercion must stop
    # that from auto-popping UAC. Guards the alias regression.
    from navig.commands import service

    class _Sentinel(Exception):
        pass

    monkeypatch.setattr(service, "_is_elevated", lambda: False)
    monkeypatch.setattr(
        service, "_relaunch_elevated",
        lambda *a: (_ for _ in ()).throw(AssertionError("must not elevate on a direct call")),
    )
    monkeypatch.setattr("navig.daemon.service_manager.task_scheduler_disable", lambda: None)

    class FakeDaemon:
        @staticmethod
        def is_running():
            raise _Sentinel  # reached ONLY if the admin block was correctly skipped

    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)

    # No args → admin is a truthy OptionInfo; coercion → False → past the admin block
    # (reaching is_running → _Sentinel), and _relaunch_elevated is never called.
    with pytest.raises(_Sentinel):
        service.service_restart()
