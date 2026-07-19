"""`navig mcp` — the external-server manager must not crash, and must persist.

Every one of these commands was broken in a way no test could see, because
nothing could REACH them: navig/commands/mcp.py was only callable from the
legacy interactive shell, so its drift against the current console_helper API
went unnoticed for a long time.

  * `mcp search` raised AttributeError: create_table() takes COLUMN DICTS, not
    bare strings — so it blew up the moment it found a result.
  * `mcp remove` raised AttributeError: it called ch.confirm(), which does not
    exist (the helper is ch.confirm_action()).
  * `mcp list` echoed the MCPServer OBJECTS — it literally printed
    "<navig.mcp_manager.MCPServer object at 0x...>".

So these tests drive the real CLI against an isolated config dir. They are the
cheap check that would have caught all three.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.mcp_cmd import mcp_app

runner = CliRunner()

_SERVER = {
    "filesystem": {
        "type": "npm",
        "package": "@modelcontextprotocol/server-filesystem",
        "command": "npx",
        "args": [],
        "env": {},
        "enabled": False,
    }
}


@pytest.fixture()
def mcp_config(tmp_path: Path, monkeypatch) -> Path:
    """An isolated ~/.navig — MCPManager reads config_dir()/mcp/servers.json."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    from navig.platform import paths

    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    (tmp_path / "mcp").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _servers(cfg: Path) -> dict:
    f = cfg / "mcp" / "servers.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _seed(cfg: Path, enabled: bool = False) -> None:
    servers = json.loads(json.dumps(_SERVER))  # deep copy
    servers["filesystem"]["enabled"] = enabled
    (cfg / "mcp" / "servers.json").write_text(json.dumps(servers), encoding="utf-8")


def test_search_renders_results_without_crashing(mcp_config):
    """Regression: create_table(columns=[str]) raised 'str' has no attribute 'get'."""
    result = runner.invoke(mcp_app, ["search", "filesystem"])
    assert result.exit_code == 0, result.output
    assert "filesystem" in result.output
    assert "navig mcp install" in result.output  # and the hint names a real command


def test_list_empty_state_tells_you_what_to_do(mcp_config):
    result = runner.invoke(mcp_app, ["list"])
    assert result.exit_code == 0, result.output
    assert "No MCP servers installed" in result.output


def test_list_shows_the_server_not_its_repr(mcp_config):
    """Regression: `mcp list` printed <navig.mcp_manager.MCPServer object at 0x…>."""
    _seed(mcp_config)
    result = runner.invoke(mcp_app, ["list"])
    assert result.exit_code == 0, result.output
    assert "filesystem" in result.output
    assert "MCPServer object" not in result.output
    assert "Disabled" in result.output


def test_enable_then_disable_persists(mcp_config):
    _seed(mcp_config, enabled=False)

    assert runner.invoke(mcp_app, ["enable", "filesystem"]).exit_code == 0
    assert _servers(mcp_config)["filesystem"]["enabled"] is True

    assert runner.invoke(mcp_app, ["disable", "filesystem"]).exit_code == 0
    assert _servers(mcp_config)["filesystem"]["enabled"] is False


def test_remove_needs_no_prompt_with_yes_and_actually_removes(mcp_config):
    """Regression: `remove` crashed on ch.confirm(); and without --yes there was
    no way to remove one non-interactively (a script would hang on the prompt)."""
    _seed(mcp_config, enabled=True)

    result = runner.invoke(mcp_app, ["remove", "filesystem", "--yes"])
    assert result.exit_code == 0, result.output
    assert _servers(mcp_config) == {}


def test_unknown_server_is_reported_not_crashed(mcp_config):
    for args in (["enable", "nope"], ["disable", "nope"], ["remove", "nope", "--yes"]):
        result = runner.invoke(mcp_app, args)
        assert result.exit_code == 0, f"{args} -> {result.output}"
        assert "not found" in result.output.lower()


def test_install_dry_run_touches_nothing(mcp_config):
    result = runner.invoke(mcp_app, ["install", "filesystem", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Would install" in result.output
    assert _servers(mcp_config) == {}, "a dry run must not write config"


def test_info_shows_one_servers_detail(mcp_config):
    """`navig mcp info <name>` renders the per-server drill-down (type / command /
    state). The renderer existed but was reachable only from the legacy shell —
    this is the cheap check that it's now wired and doesn't crash."""
    _seed(mcp_config, enabled=False)
    result = runner.invoke(mcp_app, ["info", "filesystem"])
    assert result.exit_code == 0, result.output
    assert "filesystem" in result.output
    assert "disabled" in result.output.lower()
    # it nudges toward the enable verb when the server is disabled
    assert "navig mcp enable" in result.output


def test_info_unknown_server_is_reported_not_crashed(mcp_config):
    result = runner.invoke(mcp_app, ["info", "nope"])
    assert result.exit_code == 0, result.output
    assert "not found" in result.output.lower()


def _seed_with_secret(cfg: Path, enabled: bool = True) -> None:
    """A server whose env carries a secret — to prove `--json` never leaks it."""
    servers = {
        "brave": {
            "type": "npm",
            "package": "@modelcontextprotocol/server-brave-search",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
            "env": {"BRAVE_API_KEY": "super-secret-value-xyz"},
            "enabled": enabled,
        }
    }
    (cfg / "mcp" / "servers.json").write_text(json.dumps(servers), encoding="utf-8")


def test_info_never_claims_not_running_from_a_oneshot_cli(mcp_config):
    """Honesty regression: `is_running()` only knows a process THIS process started,
    so from a one-shot CLI it is always False. `info` must NOT print "not running"
    (a state it cannot actually know) — matching `list`, which omits it for the same
    reason. The running clause appears only when a process is affirmatively alive."""
    _seed(mcp_config, enabled=True)
    result = runner.invoke(mcp_app, ["info", "filesystem"])
    assert result.exit_code == 0, result.output
    assert "not running" not in result.output.lower()


def test_info_json_is_parseable_and_never_leaks_env_values(mcp_config):
    _seed_with_secret(mcp_config, enabled=True)
    result = runner.invoke(mcp_app, ["info", "brave", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)  # must be valid JSON, not a Rich table
    assert data["name"] == "brave"
    assert data["enabled"] is True
    assert data["type"] == "npm"
    # env KEY names are exposed; the VALUE must never appear anywhere in the output
    assert data["env_keys"] == ["BRAVE_API_KEY"]
    assert "super-secret-value-xyz" not in result.output
    # running/pid are omitted — not authoritatively knowable from a one-shot CLI
    assert "running" not in data and "pid" not in data


def test_info_json_missing_server_is_null(mcp_config):
    result = runner.invoke(mcp_app, ["info", "ghost", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) is None  # a parseable "no such server"


def test_list_json_is_parseable_and_secret_free(mcp_config):
    _seed_with_secret(mcp_config, enabled=False)
    result = runner.invoke(mcp_app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list) and data[0]["name"] == "brave"
    assert "super-secret-value-xyz" not in result.output


def test_list_json_empty_is_empty_array_not_a_warning(mcp_config):
    """An agent asking for JSON gets `[]`, not the human "No MCP servers" prose."""
    result = runner.invoke(mcp_app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []
