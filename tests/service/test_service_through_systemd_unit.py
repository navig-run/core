"""`navig service` goes THROUGH a package-installed systemd unit when one manages the daemon.

The Debian packages ship `navig-agent@.service` (`User=%i`) and enable it per owner. While
that instance exists, systemd owns the daemon: killing it behind systemd's back and
respawning it by hand leaves an unsupervised daemon, and a stop that escalates to SIGKILL
counts as a failure that `Restart=on-failure` immediately undoes. So start, stop and
restart call `systemctl`, `service install` does not add a competing per-user unit, and
`stop` does NOT set the stop flag (the daemon refuses to boot while it is set, so the
unit's start at the next boot would fail in a loop).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from navig.commands import service as svc
from navig.daemon import service_manager as sm

runner = CliRunner()


class _Daemon:
    def __init__(self) -> None:
        self.killed = False

    @staticmethod
    def is_running() -> bool:
        return True

    @staticmethod
    def read_pid() -> int:
        return 4242

    def stop_running_daemon(self) -> bool:  # pragma: no cover — must not be reached
        self.killed = True
        return True


@pytest.fixture
def systemctl(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every systemctl call; make them all succeed."""
    calls: list[list[str]] = []

    def fake_run(argv, **_kw):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    return calls


def _managed(monkeypatch: pytest.MonkeyPatch, unit: str = "navig-agent@operator.service") -> None:
    monkeypatch.setattr(sm, "managed_systemd_unit", lambda: unit)


# ── detection ────────────────────────────────────────────────────────────────


class TestManagedSystemdUnit:
    def _linux(self, monkeypatch: pytest.MonkeyPatch, unit_dir: Path) -> None:
        monkeypatch.setattr(sm.sys, "platform", "linux")
        monkeypatch.setattr(sm, "has_systemd", lambda: True)
        monkeypatch.setattr(sm, "_SYSTEMD_UNIT_DIRS", (str(unit_dir),))
        monkeypatch.delenv("NAVIG_SERVICE_UNIT", raising=False)
        monkeypatch.setattr("getpass.getuser", lambda: "operator")

    def test_names_this_users_instance_when_the_template_is_enabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        (tmp_path / "navig-agent@.service").write_text("[Service]\n")
        self._linux(monkeypatch, tmp_path)
        seen: list[list[str]] = []

        def fake_run(argv, **_kw):
            seen.append(list(argv))
            return SimpleNamespace(returncode=0, stdout="enabled\n", stderr="")

        monkeypatch.setattr(sm.subprocess, "run", fake_run)
        assert sm.managed_systemd_unit() == "navig-agent@operator.service"
        assert seen[0] == ["systemctl", "is-enabled", "navig-agent@operator.service"]

    def test_no_template_means_not_managed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        self._linux(monkeypatch, tmp_path)  # empty dir: no template
        monkeypatch.setattr(sm.subprocess, "run", lambda *a, **k: pytest.fail("must not probe"))
        assert sm.managed_systemd_unit() is None

    def test_an_instance_that_is_neither_enabled_nor_active_is_not_managed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        (tmp_path / "navig-agent@.service").write_text("[Service]\n")
        self._linux(monkeypatch, tmp_path)
        monkeypatch.setattr(
            sm.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""),
        )
        assert sm.managed_systemd_unit() is None

    def test_the_unit_can_name_itself(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._linux(monkeypatch, tmp_path)
        monkeypatch.setenv("NAVIG_SERVICE_UNIT", "navig-agent@alice")
        assert sm.managed_systemd_unit() == "navig-agent@alice.service"

    def test_never_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sm.sys, "platform", "win32")
        monkeypatch.setenv("NAVIG_SERVICE_UNIT", "navig-agent@x")
        assert sm.managed_systemd_unit() is None


class TestUnitAction:
    def test_never_prompts_and_names_the_sudo_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[list[str]] = []

        def fake_run(argv, **_kw):
            seen.append(list(argv))
            return SimpleNamespace(returncode=1, stdout="", stderr="Access denied\n")

        monkeypatch.setattr(sm.subprocess, "run", fake_run)
        ok, msg = sm.systemd_unit_action("navig-agent@operator.service", "restart")
        assert ok is False
        assert seen == [["systemctl", "--no-ask-password", "restart", "navig-agent@operator.service"]]
        assert "Access denied" in msg
        assert "sudo systemctl restart navig-agent@operator.service" in msg

    def test_a_hung_systemctl_is_a_failure_not_a_hang(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(argv, **kw):
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))

        monkeypatch.setattr(sm.subprocess, "run", fake_run)
        ok, msg = sm.systemd_unit_action("u.service", "stop", timeout=1)
        assert ok is False and "did not finish" in msg


# ── the commands ─────────────────────────────────────────────────────────────


def test_restart_is_one_systemctl_restart_and_kills_nothing_itself(
    monkeypatch: pytest.MonkeyPatch, systemctl: list[list[str]],
) -> None:
    _managed(monkeypatch)
    daemon = _Daemon()
    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", daemon)
    result = runner.invoke(svc.service_app, ["restart"])
    assert result.exit_code == 0, result.output
    assert systemctl == [["systemctl", "--no-ask-password", "restart", "navig-agent@operator.service"]]
    assert daemon.killed is False
    assert "via systemd" in result.output


def test_stop_goes_through_systemd_and_leaves_no_stop_flag(
    monkeypatch: pytest.MonkeyPatch, systemctl: list[list[str]],
) -> None:
    _managed(monkeypatch)
    flagged: list[bool] = []
    monkeypatch.setattr(sm, "set_stop_flag", lambda: flagged.append(True))
    result = runner.invoke(svc.service_app, ["stop"])
    assert result.exit_code == 0, result.output
    assert systemctl == [["systemctl", "--no-ask-password", "stop", "navig-agent@operator.service"]]
    assert flagged == [], "a stop flag would make the unit's next boot-time start fail"
    assert "systemctl disable" in result.output


def test_start_never_falls_back_to_an_unsupervised_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.sys, "platform", "linux")
    _managed(monkeypatch)
    monkeypatch.setattr(sm, "systemd_unit_action", lambda unit, action, **_k: (False, "Access denied"))
    spawned: list[bool] = []
    monkeypatch.setattr(svc, "_spawn_daemon_direct", lambda: spawned.append(True))
    started, how = svc._launch_daemon(_Daemon())
    assert started is False
    assert spawned == []
    assert "systemd" in how


def test_install_does_not_add_a_competing_user_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    _managed(monkeypatch)
    monkeypatch.setattr(sm, "atomic_write_text", lambda *a, **k: pytest.fail("must not write a unit"))
    ok, msg = sm.systemd_install()
    assert ok is True
    assert "navig-agent@operator.service" in msg


def test_status_reports_the_managed_unit(
    monkeypatch: pytest.MonkeyPatch, systemctl: list[list[str]],
) -> None:
    _managed(monkeypatch)
    ok, _detail = sm.systemd_status()
    assert ok is True
    assert ["systemctl", "is-active", "navig-agent@operator.service"] in systemctl
