"""`navig config set a.b.c v` when `a.b` already holds a scalar.

The MCP trust refusal (navig/mcp/registry.py) tells an operator whose server uses the
documented short form `mcp.trust.servers.<id>: vetted` to run
`navig config set mcp.trust.servers.<id>.tools ...`. `set_config` walked into the
string and did `"vetted"["tools"] = ...`, so the suggested command raised TypeError.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import typer

from navig.commands import config as config_cmd


def _run(global_config: dict, key: str, value: str) -> MagicMock:
    manager = MagicMock()
    manager.global_config = global_config
    with (
        patch.object(config_cmd, "get_config_manager", return_value=manager),
        patch.object(config_cmd, "ch"),
        patch.object(config_cmd, "_record_config_set"),
    ):
        config_cmd.set_config(key, value)
    return manager


def test_short_trust_form_is_promoted_so_tools_can_be_scoped() -> None:
    cfg = {"mcp": {"trust": {"servers": {"acme": "vetted"}}}}
    manager = _run(cfg, "mcp.trust.servers.acme.tools", "list_issues")

    assert cfg["mcp"]["trust"]["servers"]["acme"] == {"tier": "vetted", "tools": "list_issues"}
    manager._save_global_config.assert_called_once()

    from navig.mcp import trust

    with patch.object(trust, "_mcp_trust_config", return_value=cfg["mcp"]["trust"]):
        assert trust.trust_for_server("acme") == trust.ServerTrust.VETTED


def test_other_scalar_parent_is_refused_without_writing() -> None:
    cfg = {"log_level": "DEBUG"}
    manager = MagicMock()
    manager.global_config = cfg
    with (
        patch.object(config_cmd, "get_config_manager", return_value=manager),
        patch.object(config_cmd, "ch") as ch,
        patch.object(config_cmd, "_record_config_set"),
        pytest.raises(typer.Exit) as exc,
    ):
        config_cmd.set_config("log_level.child", "x")

    assert exc.value.exit_code == 1
    ch.error.assert_called_once()
    manager._save_global_config.assert_not_called()
    assert cfg == {"log_level": "DEBUG"}
