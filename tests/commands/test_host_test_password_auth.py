"""`navig host test` on a host with a stored password tests the PASSWORD path.

Found 2026-10-16 on navig-cloud: its password lives in the vault and every real
command logs in with it (paramiko), but `host test` only ran OpenSSH with
BatchMode=yes and the key — reporting "Permission denied" for a working host.
"""

from __future__ import annotations

import pytest

from navig.commands import host as host_mod


class _CM:
    def __init__(self, cfg):
        self.cfg = cfg

    def get_active_host(self):
        return "box"

    def host_exists(self, name):
        return True

    def load_host_config(self, name):
        return dict(self.cfg)

    def is_local_host(self, name):
        return False


def _run(monkeypatch, cfg, result):
    calls = []

    class Disco:
        def __init__(self, config):
            calls.append(("disco", config.get("ssh_password")))

        def _execute_ssh(self, command):
            calls.append(("ssh", command))
            return result

    monkeypatch.setattr(host_mod, "config_manager", _CM(cfg))
    import navig.discovery as discovery

    monkeypatch.setattr(discovery, "ServerDiscovery", Disco)

    def no_openssh(*a, **k):
        raise AssertionError("the OpenSSH key-only probe must not run for a password host")

    monkeypatch.setattr("subprocess.run", no_openssh)
    return calls


def test_a_password_host_is_tested_with_its_password(monkeypatch):
    calls = _run(monkeypatch, {"host": "203.0.113.5", "user": "u", "ssh_password": "pw"},
                 (True, "Connection successful", ""))
    host_mod.test_host({"host_name": "box", "silent": True})
    assert calls[0] == ("disco", "pw") and calls[1][0] == "ssh"


def test_a_rejected_password_fails_without_printing_it(monkeypatch, capsys):
    _run(monkeypatch, {"host": "203.0.113.5", "user": "u", "ssh_password": "S3CRET-pw"},
         (False, "", "Authentication failed."))
    with pytest.raises(RuntimeError):
        host_mod.test_host({"host_name": "box"})
    out = capsys.readouterr()
    assert "S3CRET-pw" not in out.out + out.err
    assert "navig vault set vault://hosts/box/ssh" in out.out + out.err


def test_profiles_say_how_each_install_gets_secrets_and_none_serves_them_by_default():
    from navig.installer.profiles import INSTALL_PROFILES

    roles = {name: (m.get("config") or {}).get("vault.role") for name, m in INSTALL_PROFILES.items()}
    assert roles == {"cli": "client", "minimal": "client", "server": "client", "full": "local"}
    for m in INSTALL_PROFILES.values():
        assert "vault.authority.enabled" not in (m.get("config") or {})