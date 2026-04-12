from __future__ import annotations

import sys
from types import SimpleNamespace

from typer.testing import CliRunner

import navig.commands.db as db_mod
import pytest

pytestmark = pytest.mark.integration


def test_resolve_host_discovery_handles_missing_active_host(monkeypatch):
    errors: list[str] = []

    monkeypatch.setattr(db_mod.ch, "error", lambda msg: errors.append(str(msg)))

    cfg_mgr = SimpleNamespace()
    monkeypatch.setitem(
        sys.modules,
        "navig.config",
        SimpleNamespace(get_config_manager=lambda: cfg_mgr),
    )
    monkeypatch.setitem(
        sys.modules,
        "navig.cli.recovery",
        SimpleNamespace(require_active_host=lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("No active host"))),
    )
    monkeypatch.setitem(
        sys.modules,
        "navig.discovery",
        SimpleNamespace(ServerDiscovery=lambda *_a, **_kw: None),
    )

    result = db_mod._resolve_host_discovery({})

    assert result is None
    assert any("No active host" in e for e in errors)


def test_resolve_host_discovery_handles_missing_host_config(monkeypatch):
    errors: list[str] = []

    monkeypatch.setattr(db_mod.ch, "error", lambda msg: errors.append(str(msg)))

    cfg_mgr = SimpleNamespace(load_host_config=lambda _name: (_ for _ in ()).throw(FileNotFoundError))
    monkeypatch.setitem(
        sys.modules,
        "navig.config",
        SimpleNamespace(get_config_manager=lambda: cfg_mgr),
    )
    monkeypatch.setitem(
        sys.modules,
        "navig.cli.recovery",
        SimpleNamespace(require_active_host=lambda *_a, **_kw: "prod"),
    )
    monkeypatch.setitem(
        sys.modules,
        "navig.discovery",
        SimpleNamespace(ServerDiscovery=lambda *_a, **_kw: None),
    )

    result = db_mod._resolve_host_discovery({})

    assert result is None
    assert any("Host not found: prod" in e for e in errors)


def test_resolve_host_discovery_handles_missing_host_field(monkeypatch):
    errors: list[str] = []

    monkeypatch.setattr(db_mod.ch, "error", lambda msg: errors.append(str(msg)))

    cfg_mgr = SimpleNamespace(load_host_config=lambda _name: {"user": "deploy"})
    monkeypatch.setitem(
        sys.modules,
        "navig.config",
        SimpleNamespace(get_config_manager=lambda: cfg_mgr),
    )
    monkeypatch.setitem(
        sys.modules,
        "navig.cli.recovery",
        SimpleNamespace(require_active_host=lambda *_a, **_kw: "prod"),
    )
    monkeypatch.setitem(
        sys.modules,
        "navig.discovery",
        SimpleNamespace(ServerDiscovery=lambda *_a, **_kw: None),
    )

    result = db_mod._resolve_host_discovery({})

    assert result is None
    assert any("missing required 'host' or 'hostname'" in e for e in errors)


def test_resolve_host_discovery_success_uses_hostname_fallback(monkeypatch):
    captured: dict[str, object] = {}

    cfg_mgr = SimpleNamespace(
        load_host_config=lambda _name: {
            "hostname": "srv.example.com",
            "user": "deploy",
            "port": 2222,
            "ssh_key": "~/.ssh/id_rsa",
        }
    )

    class _Discovery:
        def __init__(self, ssh_config, debug_logger=None):
            captured["ssh_config"] = ssh_config
            captured["debug_logger"] = debug_logger

    monkeypatch.setitem(
        sys.modules,
        "navig.config",
        SimpleNamespace(get_config_manager=lambda: cfg_mgr),
    )
    monkeypatch.setitem(
        sys.modules,
        "navig.cli.recovery",
        SimpleNamespace(require_active_host=lambda *_a, **_kw: "prod"),
    )
    monkeypatch.setitem(
        sys.modules,
        "navig.discovery",
        SimpleNamespace(ServerDiscovery=_Discovery),
    )

    result = db_mod._resolve_host_discovery({"debug_logger": "dbg"})

    assert result is not None
    host_name, config_manager, _discovery = result
    assert host_name == "prod"
    assert config_manager is cfg_mgr
    assert captured["ssh_config"]["host"] == "srv.example.com"
    assert captured["ssh_config"]["user"] == "deploy"
    assert captured["ssh_config"]["port"] == 2222
    assert captured["debug_logger"] == "dbg"


def test_db_callback_initializes_ctx_obj_for_subcommands(monkeypatch):
    runner = CliRunner()

    captured: dict[str, object] = {}

    def _fake_db_query_cmd(query, container, user, password, database, db_type, options):
        captured["query"] = query
        captured["options"] = dict(options)

    monkeypatch.setattr(db_mod, "db_query_cmd", _fake_db_query_cmd)

    result = runner.invoke(db_mod.db_app, ["query", "SELECT 1"])

    assert result.exit_code == 0
    assert captured["query"] == "SELECT 1"
    assert isinstance(captured["options"], dict)


def test_db_dump_uses_host_discovery_guard(monkeypatch, tmp_path):
    errors: list[str] = []

    monkeypatch.setattr(db_mod.ch, "error", lambda msg: errors.append(str(msg)))
    monkeypatch.setattr(db_mod, "_resolve_host_discovery", lambda _options: None)

    db_mod.db_dump_cmd(
        database="mydb",
        output=tmp_path / "backup.sql",
        container=None,
        user="root",
        password=None,
        db_type=None,
        options={},
    )

    assert errors == []
