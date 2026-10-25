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


# ── the per-user unit (`systemctl --user`) ───────────────────────────────────
# Measured 2026-10-25 on the outpost brain: it runs under the PER-USER unit that
# `navig service install` writes without root (~/.config/systemd/user/navig-agent.service).
# managed_systemd_unit() only knows the packaged SYSTEM template, so `navig service restart`
# killed the unit's daemon and respawned one by hand (parent pid 1), leaving the unit
# inactive — an unsupervised brain that dies at the next logout or reboot.


class TestManagedUserSystemdUnit:
    def _linux_home(self, monkeypatch: pytest.MonkeyPatch, home: Path, *, unit_file: bool) -> None:
        monkeypatch.setattr(sm.sys, "platform", "linux")
        monkeypatch.setattr(sm, "has_systemd", lambda: True)
        monkeypatch.setattr(sm.Path, "home", classmethod(lambda cls: home))
        if unit_file:
            d = home / ".config" / "systemd" / "user"
            d.mkdir(parents=True)
            (d / "navig-agent.service").write_text("[Service]\n")

    def test_names_the_user_unit_when_it_is_enabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        self._linux_home(monkeypatch, tmp_path, unit_file=True)
        seen: list[list[str]] = []

        def fake_run(argv, **_kw):
            seen.append(list(argv))
            return SimpleNamespace(returncode=0, stdout="enabled\n", stderr="")

        monkeypatch.setattr(sm.subprocess, "run", fake_run)
        assert sm.managed_user_systemd_unit() == "navig-agent.service"
        assert seen[0] == ["systemctl", "--user", "is-enabled", "navig-agent.service"]

    def test_no_user_unit_file_means_no_probe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        self._linux_home(monkeypatch, tmp_path, unit_file=False)
        monkeypatch.setattr(sm.subprocess, "run", lambda *a, **k: pytest.fail("must not probe"))
        assert sm.managed_user_systemd_unit() is None
        assert not (tmp_path / ".config").exists(), "detection must not create directories"

    def test_a_user_unit_that_is_neither_enabled_nor_active_is_not_supervising(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        self._linux_home(monkeypatch, tmp_path, unit_file=True)
        monkeypatch.setattr(
            sm.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""),
        )
        assert sm.managed_user_systemd_unit() is None

    def test_never_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sm.sys, "platform", "win32")
        assert sm.managed_user_systemd_unit() is None


def test_the_packaged_system_unit_wins_over_a_user_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm, "managed_systemd_unit", lambda: "navig-agent@operator.service")
    monkeypatch.setattr(sm, "managed_user_systemd_unit", lambda: pytest.fail("must not be asked"))
    assert sm.supervising_systemd_unit() == ("navig-agent@operator.service", False)


def test_a_user_unit_action_uses_user_scope_and_no_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(argv, **_kw):
        seen.append(list(argv))
        return SimpleNamespace(returncode=1, stdout="", stderr="Failed\n")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)
    ok, msg = sm.systemd_unit_action("navig-agent.service", "restart", user=True)
    assert ok is False
    assert seen == [["systemctl", "--user", "--no-ask-password", "restart", "navig-agent.service"]]
    assert "run: systemctl --user restart navig-agent.service" in msg
    assert "sudo" not in msg


def _user_managed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm, "managed_systemd_unit", lambda: None)
    monkeypatch.setattr(sm, "managed_user_systemd_unit", lambda: "navig-agent.service")


def test_restart_under_a_user_unit_goes_through_systemctl_user(
    monkeypatch: pytest.MonkeyPatch, systemctl: list[list[str]],
) -> None:
    """The outpost defect: restart must ask systemd, never kill and respawn by hand."""
    _user_managed(monkeypatch)
    daemon = _Daemon()
    monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", daemon)
    result = runner.invoke(svc.service_app, ["restart"])
    assert result.exit_code == 0, result.output
    assert systemctl == [["systemctl", "--user", "--no-ask-password", "restart", "navig-agent.service"]]
    assert daemon.killed is False
    assert "via systemd" in result.output


def test_stop_under_a_user_unit_names_the_user_disable_and_sets_no_flag(
    monkeypatch: pytest.MonkeyPatch, systemctl: list[list[str]],
) -> None:
    _user_managed(monkeypatch)
    flagged: list[bool] = []
    monkeypatch.setattr(sm, "set_stop_flag", lambda: flagged.append(True))
    result = runner.invoke(svc.service_app, ["stop"])
    assert result.exit_code == 0, result.output
    assert systemctl == [["systemctl", "--user", "--no-ask-password", "stop", "navig-agent.service"]]
    assert flagged == []
    assert "systemctl --user disable navig-agent.service" in result.output
    assert "sudo" not in result.output


def test_start_under_a_user_unit_never_falls_back_to_a_direct_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc.sys, "platform", "linux")
    _user_managed(monkeypatch)
    calls: list[tuple] = []

    def fake_action(unit, action, **kw):
        calls.append((unit, action, kw.get("user")))
        return False, "Failed"

    monkeypatch.setattr(sm, "systemd_unit_action", fake_action)
    spawned: list[bool] = []
    monkeypatch.setattr(svc, "_spawn_daemon_direct", lambda: spawned.append(True))
    started, how = svc._launch_daemon(_Daemon())
    assert started is False
    assert spawned == []
    assert calls == [("navig-agent.service", "start", True)]
