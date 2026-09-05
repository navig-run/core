"""The hub/store MCP-server badge must match what the loader actually loads.

`navig config set mcp.clients.<name>.enabled false` stores the STRING "false", and
`bool("false")` is True — so `_mcp_servers()` (the store/deck display) must coerce it the
same way `MCPClientConfig.from_dict` does, or a disabled client shows WIRED while it never
connects. Regression for the display half of the config-bool consolidation.
"""
from __future__ import annotations


def test_mcp_client_string_disabled_shows_unwired(monkeypatch):
    import navig.config as ncfg
    import navig.plugins.package as pkg
    from navig.hub.aggregator import _mcp_servers

    class _FakeCM:
        global_config = {
            "mcp": {
                "clients": {
                    "off_client": {"enabled": "false", "command": "x"},  # the footgun
                    "on_client": {"enabled": "true", "command": "x"},
                    "default_client": {"command": "x"},  # no 'enabled' → default True
                }
            }
        }

    monkeypatch.setattr(ncfg, "get_config_manager", lambda: _FakeCM())
    # Skip real plugin scanning so the test only exercises the config-clients branch.
    monkeypatch.setattr(pkg, "installed_plugin_roots", lambda: [])

    items = {i.id: i for i in _mcp_servers()}
    assert items["mcp:off_client"].state == "unwired"   # was "wired" pre-fix
    assert items["mcp:on_client"].state == "wired"
    assert items["mcp:default_client"].state == "wired"
