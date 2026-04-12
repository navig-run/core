from __future__ import annotations

from types import SimpleNamespace

import pytest

from navig.tunnel import TunnelManager

pytestmark = pytest.mark.integration


@pytest.fixture()
def tunnel_manager(tmp_path):
    config = SimpleNamespace(
        tunnels_file=tmp_path / "tunnels.json",
        log_file=tmp_path / "tunnel.log",
        global_config={},
        load_server_config=lambda _name: {
            "host": "remote.example.com",
            "user": "deploy",
            "database": {"local_tunnel_port": 3307, "remote_port": 3306},
        },
        get_active_server=lambda: "prod",
    )
    return TunnelManager(config)


def test_start_tunnel_uses_resolved_ssh_binary(monkeypatch, tunnel_manager):
    captured: dict[str, object] = {}

    class _Pipe:
        def close(self):
            return None

    class _Proc:
        def __init__(self):
            self.stdout = _Pipe()
            self.stderr = _Pipe()
            self.stdin = _Pipe()

    def _fake_popen(args, **_kwargs):
        captured["args"] = args
        return _Proc()

    monkeypatch.setattr("navig.tunnel._resolve_ssh_bin", lambda: "/usr/bin/ssh")
    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    monkeypatch.setattr(tunnel_manager, "_find_tunnel_process", lambda *_a, **_kw: 12345)
    monkeypatch.setattr(tunnel_manager, "_test_port", lambda *_a, **_kw: True)

    info = tunnel_manager.start_tunnel("prod")

    assert info["pid"] == 12345
    assert captured["args"][0] == "/usr/bin/ssh"


def test_start_tunnel_requires_database_mapping(monkeypatch, tmp_path):
    config = SimpleNamespace(
        tunnels_file=tmp_path / "tunnels.json",
        log_file=tmp_path / "tunnel.log",
        global_config={},
        load_server_config=lambda _name: {"host": "remote.example.com", "user": "deploy"},
        get_active_server=lambda: "prod",
    )
    manager = TunnelManager(config)

    with pytest.raises(ValueError) as exc_info:
        manager.start_tunnel("prod")

    assert "must include a 'database' mapping" in str(exc_info.value)


@pytest.mark.parametrize(
    "server_config",
    [
        {"host": "", "user": "deploy", "database": {}},
        {"host": "remote.example.com", "user": "", "database": {}},
    ],
)
def test_start_tunnel_requires_user_and_host(monkeypatch, tmp_path, server_config):
    config = SimpleNamespace(
        tunnels_file=tmp_path / "tunnels.json",
        log_file=tmp_path / "tunnel.log",
        global_config={},
        load_server_config=lambda _name: server_config,
        get_active_server=lambda: "prod",
    )
    manager = TunnelManager(config)

    with pytest.raises(ValueError) as exc_info:
        manager.start_tunnel("prod")

    assert "must include non-empty 'user' and 'host'" in str(exc_info.value)
