"""A systemctl binary is not a running systemd.

The defect these pin: `has_systemd()` answered "yes" on a container or a WSL distro
without `systemd=true`, because it only checked `shutil.which("systemctl")`. Every
`navig service install` there then failed with "System has not been booted with systemd
as init system", which reads like a NAVIG bug rather than a host fact.
"""

from __future__ import annotations

from navig.daemon import service_manager as sm


def test_systemctl_without_a_running_systemd_is_not_systemd(monkeypatch):
    monkeypatch.setattr(sm.shutil, "which", lambda _name: "/usr/bin/systemctl")
    monkeypatch.setattr(sm, "_systemd_is_running", lambda: False)
    assert sm.has_systemd() is False


def test_systemd_running_means_systemd(monkeypatch):
    monkeypatch.setattr(sm.shutil, "which", lambda _name: "/usr/bin/systemctl")
    monkeypatch.setattr(sm, "_systemd_is_running", lambda: True)
    assert sm.has_systemd() is True


def test_no_systemctl_binary_is_not_systemd(monkeypatch):
    monkeypatch.setattr(sm.shutil, "which", lambda _name: None)
    monkeypatch.setattr(sm, "_systemd_is_running", lambda: True)
    assert sm.has_systemd() is False


def test_linux_without_systemd_falls_back_to_manual(monkeypatch):
    monkeypatch.setattr(sm.sys, "platform", "linux")
    monkeypatch.setattr(sm, "has_systemd", lambda: False)
    assert sm.detect_best_method() == "manual"


def test_the_manual_hint_names_a_supervisor_this_host_can_run(monkeypatch, tmp_path):
    monkeypatch.setattr(sm.sys, "platform", "linux")
    version = tmp_path / "version"
    version.write_text("Linux version 5.15.153.1-microsoft-standard-WSL2", encoding="utf-8")
    monkeypatch.setattr(sm, "_PROC_VERSION_PATH", version)
    hint = sm.manual_supervision_hint()
    assert "wsl.conf" in hint
    assert "systemd=true" in hint


def test_the_manual_hint_on_a_container_names_an_external_supervisor(monkeypatch, tmp_path):
    monkeypatch.setattr(sm.sys, "platform", "linux")
    version = tmp_path / "version"
    version.write_text("Linux version 6.1.0-generic", encoding="utf-8")
    monkeypatch.setattr(sm, "_PROC_VERSION_PATH", version)
    hint = sm.manual_supervision_hint()
    assert "supervisord" in hint
    assert "navig.daemon.entry" in hint


def test_installing_with_no_manager_explains_itself(monkeypatch):
    monkeypatch.setattr(sm.sys, "platform", "linux")
    monkeypatch.setattr(sm, "has_systemd", lambda: False)
    monkeypatch.setattr(sm, "manual_supervision_hint", lambda: "\nHINT")
    ok, message = sm.install()
    assert ok is False
    assert message.endswith("HINT")
