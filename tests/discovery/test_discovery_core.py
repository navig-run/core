from __future__ import annotations

import navig.discovery as disc_mod
import pytest

pytestmark = pytest.mark.integration


def test_server_discovery_requires_host_and_user():
    try:
        disc_mod.ServerDiscovery({"host": "", "user": "root"})
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "host" in str(exc)

    try:
        disc_mod.ServerDiscovery({"host": "example.com", "user": ""})
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "user" in str(exc)


def test_build_ssh_command_uses_resolved_binary(monkeypatch):
    monkeypatch.setattr(disc_mod, "_resolve_ssh_bin", lambda: "/usr/bin/ssh")

    discovery = disc_mod.ServerDiscovery({"host": "example.com", "user": "root", "port": 2222})
    cmd = discovery._build_ssh_command("echo ok")

    assert cmd[0] == "/usr/bin/ssh"
    assert "root@example.com" in cmd


def test_execute_ssh_password_mode_without_paramiko_returns_clear_error(monkeypatch):
    discovery = disc_mod.ServerDiscovery(
        {"host": "example.com", "user": "root", "ssh_password": "secret"}
    )

    monkeypatch.setattr(disc_mod, "_get_paramiko", lambda: False)

    success, stdout, stderr = discovery._execute_ssh("echo test")

    assert success is False
    assert stdout == ""
    assert "requires paramiko" in stderr


def test_execute_ssh_password_mode_uses_paramiko_path_when_available(monkeypatch):
    discovery = disc_mod.ServerDiscovery(
        {"host": "example.com", "user": "root", "ssh_password": "secret"}
    )

    monkeypatch.setattr(disc_mod, "_get_paramiko", lambda: object())
    monkeypatch.setattr(
        discovery,
        "_execute_ssh_paramiko",
        lambda cmd: (True, f"ok:{cmd}", ""),
    )

    success, stdout, stderr = discovery._execute_ssh("uname -a")

    assert success is True
    assert stdout == "ok:uname -a"
    assert stderr == ""
