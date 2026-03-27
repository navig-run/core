"""Regression tests for recent onboarding + JSON output enhancements.

These tests focus on:
- `navig status` JSON/plain output stability
- `navig quickstart` creating a project `.navig/` directory
- short aliases (e.g. `navig h list`)
- JSON envelope output for representative commands

They intentionally avoid real SSH/network operations.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _invoke_cli(args: list[str], capsys) -> tuple[int, str, str]:
    """Invoke Typer app in-process and return (exit_code, stdout, stderr)."""
    from navig.cli import app

    exit_code = 0
    try:
        app(args, standalone_mode=False)
    except SystemExit as e:
        # Typer uses SystemExit for `typer.Exit()` and for errors.
        exit_code = int(e.code or 0)

    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch):
    """Isolated working dir + HOME for CLI/config singletons."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    # Reset ConfigManager singleton between tests.
    from navig.config import reset_config_manager

    reset_config_manager()

    # Reset CLI-level cached config manager.
    import navig.cli as cli

    cli._config_manager = None
    cli._NO_CACHE = False
    cli._register_external_commands(register_all=True)

    return tmp_path


def _write_host_config(project_root: Path, host_name: str = "testhost") -> None:
    """Create a minimal host config under the project's `.navig/hosts/`."""
    from navig.config import ConfigManager

    cm = ConfigManager(config_dir=project_root / ".navig")
    cm.save_host_config(
        host_name,
        {
            "name": host_name,
            "host": "example.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/id_ed25519",
            "apps": {},
        },
    )


def _write_host_with_apps(project_root: Path, host_name: str = "testhost") -> None:
    """Create a host config with a couple apps (valid shape for load_app_config)."""
    from navig.config import ConfigManager

    cm = ConfigManager(config_dir=project_root / ".navig")
    cm.save_host_config(
        host_name,
        {
            "name": host_name,
            "host": "example.com",
            "port": 22,
            "user": "root",
            "ssh_key": "~/.ssh/id_ed25519",
            "default_app": "site",
            "apps": {
                "site": {
                    "webserver": {"type": "nginx"},
                    "database": {"type": "mysql", "name": "site_db"},
                    "paths": {"root": "/var/www/site"},
                },
                "api": {
                    "webserver": {"type": "nginx"},
                    "database": {"type": "postgresql", "name": "api_db"},
                    "paths": {"root": "/var/www/api"},
                },
            },
        },
    )


def test_status_json_is_valid_envelope(isolated_project: Path, capsys):
    code, out, err = _invoke_cli(["status", "--json"], capsys)
    assert code == 0
    assert err == ""

    payload = json.loads(out)
    assert payload["schema_version"] == "1.0.0"
    assert "active" in payload
    assert "tunnel" in payload
    assert set(payload["active"].keys()) == {"host", "app"}


def test_status_plain_is_single_line(isolated_project: Path, capsys):
    code, out, err = _invoke_cli(["status", "--plain"], capsys)
    assert code == 0
    assert err == ""

    line = out.strip().splitlines()
    assert len(line) == 1
    assert line[0].startswith("host=")
    assert " app=" in line[0]
    assert " tunnel=" in line[0]


def test_quickstart_creates_project_navig_dir(
    isolated_project: Path, capsys, monkeypatch
):
    # Avoid local discovery (which can be environment-dependent) by forcing the prompt to decline.
    from navig.commands import quickstart as quickstart_mod

    monkeypatch.setattr(quickstart_mod.ch, "confirm_action", lambda *a, **k: False)

    assert not (isolated_project / ".navig").exists()

    code, out, err = _invoke_cli(["--quiet", "quickstart"], capsys)
    assert code == 0
    assert err == ""

    assert (isolated_project / ".navig").exists()


def test_host_list_json_has_expected_shape(isolated_project: Path, capsys):
    _write_host_config(isolated_project, "alpha")

    # Ensure subsequent `get_config_manager()` calls re-detect the project `.navig/`.
    from navig.config import reset_config_manager

    reset_config_manager()
    # Rebind module-level cached config manager used by host commands.
    from navig.commands import host as host_mod
    from navig.config import get_config_manager

    host_mod.config_manager = get_config_manager(force_new=True)

    code, out, err = _invoke_cli(["host", "list", "--json"], capsys)
    assert code == 0
    assert err == ""

    payload = json.loads(out)
    assert payload["schema_version"] == "1.0.0"
    assert payload["command"] == "host.list"
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["hosts"][0]["name"] == "alpha"


def test_alias_h_host_list_plain_outputs_names(isolated_project: Path, capsys):
    _write_host_config(isolated_project, "alpha")
    _write_host_config(isolated_project, "bravo")

    from navig.config import reset_config_manager

    reset_config_manager()
    # Rebind module-level cached config manager used by host commands.
    from navig.commands import host as host_mod
    from navig.config import get_config_manager

    host_mod.config_manager = get_config_manager(force_new=True)

    code, out, err = _invoke_cli(["h", "list", "--plain"], capsys)
    assert code == 0
    assert err == ""

    names = {line.strip() for line in out.splitlines() if line.strip()}
    assert {"alpha", "bravo"}.issubset(names)


def test_run_json_envelope_captures_stdout(isolated_project: Path, capsys, monkeypatch):
    # Create a host config so `load_host_config()` succeeds.
    _write_host_config(isolated_project, "alpha")

    from navig.config import reset_config_manager

    reset_config_manager()

    # Patch RemoteOperations to avoid SSH.
    import navig.remote as remote_module

    class DummyRemoteOps:
        def __init__(self, _config_manager):
            pass

        def execute_command(self, _cmd, _host_cfg, capture_output=False):
            assert capture_output is True
            return SimpleNamespace(returncode=0, stdout="hello\n", stderr="")

    monkeypatch.setattr(remote_module, "RemoteOperations", DummyRemoteOps)

    # Patch confirmation to always allow.
    from navig.commands import remote as remote_cmd

    monkeypatch.setattr(remote_cmd.ch, "confirm_operation", lambda *a, **k: True)

    remote_cmd.run_remote_command(
        "echo hello",
        options={"host": "alpha", "yes": True, "json": True},
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == "1.0.0"
    assert payload["command"] == "run"
    assert payload["success"] is True
    assert payload["host"] == "alpha"
    assert payload["stdout"] == "hello\n"


def test_app_list_json_envelope_all_hosts(isolated_project: Path, capsys):
    _write_host_with_apps(isolated_project, "alpha")

    from navig.commands import app as app_mod
    from navig.config import get_config_manager, reset_config_manager

    reset_config_manager()
    app_mod.config_manager = get_config_manager(force_new=True)

    app_mod.list_apps({"all": True, "json": True})
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["schema_version"] == "1.0.0"
    assert payload["command"] == "app.list"
    assert payload["success"] is True
    assert payload["scope"] == "all-hosts"
    assert payload["count"] >= 2
    apps = {(a["host"], a["app"]) for a in payload["apps"]}
    assert ("alpha", "site") in apps
    assert ("alpha", "api") in apps


def test_app_show_json_envelope(isolated_project: Path, capsys):
    _write_host_with_apps(isolated_project, "alpha")

    from navig.commands import app as app_mod
    from navig.config import get_config_manager, reset_config_manager

    reset_config_manager()
    app_mod.config_manager = get_config_manager(force_new=True)

    app_mod.show_app({"host": "alpha", "app_name": "site", "json": True})
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["schema_version"] == "1.0.0"
    assert payload["command"] == "app.show"
    assert payload["success"] is True
    assert payload["host"] == "alpha"
    assert payload["app"] == "site"
    assert payload["config"]["webserver"]["type"] == "nginx"


def test_file_list_json_envelope(isolated_project: Path, capsys, monkeypatch):
    _write_host_config(isolated_project, "alpha")
    from navig.config import reset_config_manager

    reset_config_manager()

    import navig.remote as remote_module

    class DummyRemoteOps:
        def __init__(self, _config_manager):
            pass

        def execute_command(self, cmd, _host_cfg, capture_output=False):
            if cmd.startswith("find "):
                # name|type|size|mtime
                return SimpleNamespace(
                    returncode=0,
                    stdout="foo.txt|f|12|2025-12-26T00:00:00\nsubdir|d|4096|2025-12-26T00:00:01\n",
                    stderr="",
                )
            if cmd.startswith("ls "):
                return SimpleNamespace(
                    returncode=0, stdout="foo.txt\nsubdir\n", stderr=""
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_module, "RemoteOperations", DummyRemoteOps)

    from navig.commands.files_advanced import list_dir_cmd

    list_dir_cmd("/var/www", {"host": "alpha", "json": True, "quiet": True})
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["schema_version"] == "1.0.0"
    assert payload["command"] == "file.list"
    assert payload["success"] is True
    assert payload["path"] == "/var/www"
    assert payload["count"] == 2
    names = {e["name"] for e in payload["entries"]}
    assert {"foo.txt", "subdir"}.issubset(names)


def test_file_show_json_envelope(isolated_project: Path, capsys, monkeypatch):
    _write_host_config(isolated_project, "alpha")
    from navig.config import reset_config_manager

    reset_config_manager()

    import navig.remote as remote_module

    class DummyRemoteOps:
        def __init__(self, _config_manager):
            pass

        def execute_command(self, cmd, _host_cfg, capture_output=False):
            if cmd.startswith("test -f "):
                return SimpleNamespace(returncode=0, stdout="exists\n", stderr="")
            if cmd.startswith("cat "):
                return SimpleNamespace(returncode=0, stdout="hello file\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_module, "RemoteOperations", DummyRemoteOps)

    from navig.commands.files_advanced import cat_file_cmd

    cat_file_cmd("/etc/hosts", {"host": "alpha", "json": True, "quiet": True})
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["schema_version"] == "1.0.0"
    assert payload["command"] == "file.show"
    assert payload["success"] is True
    assert payload["path"] == "/etc/hosts"
    assert payload["stdout"] == "hello file\n"


def test_db_list_json_envelope(isolated_project: Path, capsys, monkeypatch):
    _write_host_config(isolated_project, "alpha")
    from navig.config import reset_config_manager

    reset_config_manager()

    import navig.discovery as discovery_module

    class DummyDiscovery:
        def __init__(self, _ssh_config, debug_logger=None):
            pass

    monkeypatch.setattr(discovery_module, "ServerDiscovery", DummyDiscovery)

    from navig.commands import db as db_mod

    monkeypatch.setattr(db_mod, "_detect_db_type", lambda _d, _c=None: "mysql")
    monkeypatch.setattr(
        db_mod,
        "_execute_db_query",
        lambda *_a, **_k: (True, "mysql\ninformation_schema\n", ""),
    )

    db_mod.db_list_cmd(
        container=None,
        user="root",
        password=None,
        db_type=None,
        options={"host": "alpha", "json": True},
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["schema_version"] == "1.0.0"
    assert payload["command"] == "db.list"
    assert payload["success"] is True
    assert payload["host"] == "alpha"
    assert payload["db_type"] == "mysql"
    assert "mysql" in payload["databases"]


def test_db_query_json_envelope(isolated_project: Path, capsys, monkeypatch):
    _write_host_config(isolated_project, "alpha")
    from navig.config import reset_config_manager

    reset_config_manager()

    import navig.discovery as discovery_module

    class DummyDiscovery:
        def __init__(self, _ssh_config, debug_logger=None):
            pass

    monkeypatch.setattr(discovery_module, "ServerDiscovery", DummyDiscovery)

    from navig.commands import db as db_mod

    monkeypatch.setattr(db_mod, "_detect_db_type", lambda _d, _c=None: "mysql")
    monkeypatch.setattr(
        db_mod, "_execute_db_query", lambda *_a, **_k: (True, "1\n", "")
    )

    db_mod.db_query_cmd(
        query="SELECT 1",
        container=None,
        user="root",
        password=None,
        database=None,
        db_type=None,
        options={"host": "alpha", "json": True, "quiet": True},
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["schema_version"] == "1.0.0"
    assert payload["command"] == "db.query"
    assert payload["success"] is True
    assert payload["query"] == "SELECT 1"
    assert payload["stdout"] == "1\n"


def test_tunnel_show_json_envelope(isolated_project: Path, capsys, monkeypatch):
    # Tunnel command uses module-level tunnel_manager; patch it to avoid touching the OS.
    from navig.commands import tunnel as tunnel_mod

    class DummyTunnelMgr:
        def get_tunnel_status(self, _server_name):
            return {"pid": 123, "local_port": 15432, "server": "alpha"}

    monkeypatch.setattr(tunnel_mod, "tunnel_manager", DummyTunnelMgr())

    tunnel_mod.show_tunnel_status({"app": "alpha", "json": True})
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["schema_version"] == "1.0.0"
    assert payload["command"] == "tunnel.show"
    assert payload["success"] is True
    assert payload["server"] == "alpha"
    assert payload["running"] is True
    assert payload["tunnel"]["pid"] == 123
