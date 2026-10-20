"""The hub/store MCP-server badge must match what the gateway actually runs.

Two truths are pinned here:

* `navig config set mcp.clients.<name>.enabled false` stores the STRING "false", and
  `bool("false")` is True — so the display must coerce it the same way
  `MCPClientConfig.from_dict` does (the display half of the config-bool consolidation).
* Only `mcp.servers` is connected by the gateway at boot. `mcp.clients` and plugin
  `.mcp.json` servers are read only by `MCPClientManager.start()`, which no production
  path calls — so they used to be badged WIRED while never running. They are UNWIRED
  now, with a description that says why, and `mcp.servers` (missing from the store
  entirely before) is listed and wired when enabled.
"""
from __future__ import annotations


def _items(monkeypatch, mcp_cfg: dict) -> dict:
    import navig.config as ncfg
    import navig.plugins.package as pkg
    from navig.hub.aggregator import _mcp_servers

    class _FakeCM:
        global_config = {"mcp": mcp_cfg}

    monkeypatch.setattr(ncfg, "get_config_manager", lambda: _FakeCM())
    # Skip real plugin scanning so the test only exercises the config branches.
    monkeypatch.setattr(pkg, "installed_plugin_roots", lambda: [])
    return {i.id: i for i in _mcp_servers()}


def test_mcp_client_string_disabled_shows_disabled(monkeypatch):
    items = _items(monkeypatch, {
        "clients": {
            "off_client": {"enabled": "false", "command": "x"},  # the footgun
            "on_client": {"enabled": "true", "command": "x"},
            "default_client": {"command": "x"},  # no 'enabled' → default True
        }
    })
    assert "disabled" in items["mcp:off_client"].description
    assert "not started" in items["mcp:on_client"].description
    assert "not started" in items["mcp:default_client"].description


def test_mcp_clients_are_never_claimed_wired(monkeypatch):
    items = _items(monkeypatch, {"clients": {"c": {"command": "x", "enabled": True}}})
    assert items["mcp:c"].state == "unwired"


def test_mcp_servers_list_is_shown_and_wired_when_enabled(monkeypatch):
    items = _items(monkeypatch, {
        "servers": [
            {"name": "live", "type": "stdio", "command": "x"},
            {"name": "off", "type": "stdio", "command": "x", "enabled": "false"},
        ],
        # A same-named client must not shadow the entry the gateway really runs.
        "clients": {"live": {"command": "y"}},
    })
    assert items["mcp:live"].state == "wired"
    assert "connected by the gateway" in items["mcp:live"].description
    assert items["mcp:off"].state == "unwired"
