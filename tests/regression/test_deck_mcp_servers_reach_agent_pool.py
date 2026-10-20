"""An MCP server added in the Deck / NAVIG OS must reach the brain agent's MCP pool.

The Deck writes ``mcp.servers`` (Claude Code's .mcp.json shape: ``type`` + ``command``/
``args``), the gateway connects that list at boot, but ``MCPClientPool`` read only
``agent.mcp.servers`` — so a server the user added in the UI was invisible to the brain's
own agent, with nothing reporting it.
"""

from __future__ import annotations

import pytest

from navig.agent.mcp_client import MCPClientPool


@pytest.fixture
def config(monkeypatch):
    values: dict = {}

    def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr("navig.config.get", fake_get)
    monkeypatch.delenv("NAVIG_MCP_SERVERS", raising=False)
    return values


def test_deck_servers_are_translated_into_the_pool(config):
    config["mcp"] = {"servers": [
        {"name": "linear", "type": "http", "url": "https://mcp.linear.app/mcp"},
        {"name": "files", "type": "stdio", "command": "npx", "args": ["-y", "@x/files"], "env": {"A": "1"}},
    ]}
    by_name = {c.name: c for c in MCPClientPool()._load_server_configs()}
    assert by_name["linear"].transport == "http"
    assert by_name["linear"].url == "https://mcp.linear.app/mcp"
    assert by_name["files"].transport == "stdio"
    assert by_name["files"].command == ["npx", "-y", "@x/files"]
    assert by_name["files"].env == {"A": "1"}


def test_agent_config_wins_a_name_clash(config):
    config["agent.mcp"] = {"servers": [{"name": "linear", "transport": "http", "url": "https://agent.example/mcp"}]}
    config["mcp"] = {"servers": [{"name": "linear", "type": "http", "url": "https://deck.example/mcp"}]}
    configs = MCPClientPool()._load_server_configs()
    assert [c.url for c in configs if c.name == "linear"] == ["https://agent.example/mcp"]


def test_malformed_deck_entries_are_skipped_not_fatal(config):
    config["mcp"] = {"servers": [
        "not-a-dict",
        {"name": "", "type": "http", "url": "https://x"},
        {"name": "no-url", "type": "http"},
        {"name": "weird", "type": "carrier-pigeon"},
        {"name": "ok", "type": "http", "url": "https://ok.example/mcp"},
    ]}
    assert [c.name for c in MCPClientPool()._load_server_configs()] == ["ok"]


def test_no_deck_servers_changes_nothing(config):
    config["agent.mcp"] = {"servers": [{"name": "a", "transport": "stdio", "command": ["a"]}]}
    assert [c.name for c in MCPClientPool()._load_server_configs()] == ["a"]
