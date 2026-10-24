"""Transport-aware policy (S2): authentication is required, authorisation is separate.

These tests exercise the startup decision (which happens before any socket) and the gate
decision (which happens before any handler). They do not start a real server or open a
port.
"""

from __future__ import annotations

from typing import Any

import pytest

import navig.mcp_server as srv
from navig.mcp.routing import COMPACT_TOOL_NAMES
from navig.mcp_server import MCPProtocolHandler, TransportContext, is_loopback


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {}

    def fake_http(handler, host, port, token):
        box.update(handler=handler, token=token, host=host)

    def fake_ws(handler, port, token, host):
        box.update(handler=handler, token=token, host=host)

    monkeypatch.setattr(srv, "_run_http_server", fake_http)
    monkeypatch.setattr(srv, "_run_websocket_server", fake_ws)
    monkeypatch.setattr(srv.MCPProtocolHandler, "run_stdio", lambda self: box.update(handler=self))
    return box


def test_loopback_detection_is_exact() -> None:
    assert is_loopback("127.0.0.1")
    assert is_loopback("127.0.1.2")
    assert is_loopback("localhost")
    assert is_loopback("::1")
    assert not is_loopback("0.0.0.0")
    assert not is_loopback("10.0.0.5")
    assert not is_loopback("127.example.com")


def test_non_loopback_without_token_is_refused(captured: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="without --token"):
        srv.start_mcp_server(mode="http", host="0.0.0.0", profile="default")


def test_loopback_without_token_serves_read_only_tools(captured: dict[str, Any]) -> None:
    srv.start_mcp_server(mode="http", host="127.0.0.1", profile="default")
    handler = captured["handler"]
    names = set(handler.advertised_tools())
    assert names == {
        "navig_fleet",
        "navig_knowledge",
        "navig_recall",
        "navig_memory",
        "navig_runtime",
        "navig_automation",
        "navig_browser_debug",
        "navig_connectors",
        "navig_capabilities",
    }
    for name in names:
        annotations = handler.tools[name]["annotations"]
        assert annotations["readOnlyHint"] is True
        assert annotations["destructiveHint"] is False
    assert "navig_runtime_action" not in names
    assert "navig_run" not in names  # executes commands: never on a token-less exposure
    assert not handler._transport.token_present


def test_loopback_without_token_hides_the_full_profile_actions(captured: dict[str, Any]) -> None:
    srv.start_mcp_server(mode="http", host="127.0.0.1", profile="full")
    handler = captured["handler"]
    for tool in handler.advertised_tools().values():
        annotations = tool["annotations"]
        assert annotations["readOnlyHint"] is True and annotations["destructiveHint"] is False


def test_insecure_local_is_opt_in_and_loopback_only(captured: dict[str, Any]) -> None:
    srv.start_mcp_server(mode="http", host="127.0.0.1", profile="default", insecure_local=True)
    assert set(captured["handler"].advertised_tools()) == set(COMPACT_TOOL_NAMES)
    with pytest.raises(ValueError, match="loopback bind"):
        srv.start_mcp_server(
            mode="http", host="0.0.0.0", token="t", profile="default", insecure_local=True
        )


def test_token_keeps_the_compact_surface(captured: dict[str, Any]) -> None:
    srv.start_mcp_server(mode="http", host="127.0.0.1", token="secret", profile="default")
    handler = captured["handler"]
    assert set(handler.advertised_tools()) == set(COMPACT_TOOL_NAMES)
    assert handler._transport.token_present


def test_websocket_generates_a_token_before_exposing(captured: dict[str, Any]) -> None:
    srv.start_mcp_server(mode="websocket", host="0.0.0.0", profile="default")
    assert captured["token"]
    assert captured["handler"]._transport.token_present
    assert set(captured["handler"].advertised_tools()) == set(COMPACT_TOOL_NAMES)


def test_remote_exposure_requires_authentication(captured: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="requires authentication"):
        srv.start_mcp_server(mode="http", host="127.0.0.1", profile="default", exposure="remote")


def test_stdio_declared_remote_gets_the_remote_context(captured: dict[str, Any]) -> None:
    srv.start_mcp_server(mode="stdio", profile="default", exposure="remote")
    handler = captured["handler"]
    assert handler._transport.exposure == "remote"
    assert handler._transport.mode == "stdio"


def _fake_original(handler: MCPProtocolHandler, name: str, result: Any) -> None:
    handler._tool_handlers[name] = lambda _server, _args: result


def test_remote_dangerous_is_denied_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = MCPProtocolHandler(
        profile="default",
        transport=TransportContext(mode="stdio", exposure="remote"),
    )
    monkeypatch.setattr(handler._config, "get", lambda key, default=None: default)
    _fake_original(handler, "navig_runtime_mission_action", {"ok": True})
    with pytest.raises(PermissionError, match="mcp.remote_dangerous"):
        handler._execute_tool(
            "navig_runtime_action",
            {"action": "mission_action", "mission_id": "m", "transition": "start"},
        )


def test_remote_dangerous_runs_only_with_explicit_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = MCPProtocolHandler(
        profile="default",
        transport=TransportContext(mode="http", host="0.0.0.0", exposure="remote"),
    )

    def fake_get(key, default=None):
        return "approve" if key == "mcp.remote_dangerous" else default

    monkeypatch.setattr(handler._config, "get", fake_get)
    _fake_original(handler, "navig_runtime_mission_action", {"ok": True})
    from navig.tools.approval import ApprovalDecision

    monkeypatch.setattr(
        "navig.tools.approval.check_sync", lambda *a, **k: ApprovalDecision.APPROVED
    )
    result = handler._execute_tool(
        "navig_runtime_action",
        {"action": "mission_action", "mission_id": "m", "transition": "start"},
    )
    assert result == {"ok": True}


def test_stdio_local_keeps_single_operator_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = MCPProtocolHandler(profile="default")
    _fake_original(handler, "navig_runtime_mission_action", {"ok": True})
    from navig.tools.approval import ApprovalDecision

    monkeypatch.setattr(
        "navig.tools.approval.check_sync", lambda *a, **k: ApprovalDecision.APPROVED
    )
    assert handler._execute_tool(
        "navig_runtime_action",
        {"action": "mission_action", "mission_id": "m", "transition": "start"},
    ) == {"ok": True}


def test_read_only_surface_rejects_hidden_actions_through_tools_call(
    captured: dict[str, Any],
) -> None:
    srv.start_mcp_server(mode="http", host="127.0.0.1", profile="default")
    handler = captured["handler"]
    result = handler._handle_tools_call(
        {"name": "navig_runtime_action", "arguments": {"action": "mission_action"}}
    )
    assert result.get("isError") is True
    assert "Unknown tool" in result["content"][0]["text"]


def test_config_schema_declares_the_mcp_security_keys() -> None:
    # Declared in the schema or the filtered config view drops them — the failure mode
    # is a security setting that exists in YAML and is never read.
    from navig.core.config_schema import GlobalConfig

    config = GlobalConfig()
    assert config.mcp.remote_dangerous == "deny"
    assert config.mcp.files.enforce == "auto"
    assert config.mcp.files.roots == []
    assert config.mcp.cors_origins == []


def test_config_schema_rejects_an_unknown_remote_policy() -> None:
    import pytest as _pytest
    from pydantic import ValidationError

    from navig.core.config_schema import GlobalConfig

    with _pytest.raises(ValidationError):
        GlobalConfig(mcp={"remote_dangerous": "allow"})


def test_env_exposure_declares_remote_for_a_stdio_container(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A platform that overrides the launch command must not be able to downgrade trust:
    # the image declares its exposure once in the environment.
    monkeypatch.setenv("NAVIG_MCP_EXPOSURE", "remote")
    srv.start_mcp_server(mode="stdio", profile="default")
    handler = captured["handler"]
    assert handler._transport.exposure == "remote"
    assert handler._transport.mode == "stdio"


def test_explicit_flag_wins_over_the_environment(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NAVIG_MCP_EXPOSURE", "remote")
    srv.start_mcp_server(mode="stdio", profile="default", exposure="local")
    assert captured["handler"]._transport.exposure == "local"


def test_invalid_env_exposure_is_refused(
    captured: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NAVIG_MCP_EXPOSURE", "public")
    with pytest.raises(ValueError, match="NAVIG_MCP_EXPOSURE"):
        srv.start_mcp_server(mode="stdio", profile="default")
