"""Contract gate for the compact MCP surface.

The router definitions are new public tools, so they are held to the same definition
target as the catalogue: every one scores at least 4.5 on the local scorer, every
read-only router contains only read-only originals, and action/annotation composites are
truthful. The full catalogue's own gates are untouched elsewhere.
"""

from __future__ import annotations

import json

from navig.mcp.definition_score import score_catalogue
from navig.mcp.routing import COMPACT_TOOL_NAMES, ROUTER_SPECS
from navig.mcp_server import MCPProtocolHandler

TARGET = 4.5


def _handler() -> MCPProtocolHandler:
    return MCPProtocolHandler(profile="default")


def test_exactly_seventeen_domains() -> None:
    # 15 core domains + navig_capabilities (discovery) + navig_run (installed CLI commands).
    assert len(COMPACT_TOOL_NAMES) == 17
    assert len(set(COMPACT_TOOL_NAMES)) == 17
    assert set(COMPACT_TOOL_NAMES) == {spec.name for spec in ROUTER_SPECS.values()}


def test_no_generic_dispatcher_tool_exists() -> None:
    names = {name.lower() for name in COMPACT_TOOL_NAMES}
    assert "navig_call" not in names
    assert not any("dispatch" in name or "invoke" in name for name in names)


def test_every_router_definition_meets_the_definition_target() -> None:
    handler = _handler()
    report = score_catalogue(handler.advertised_tools())
    assert report["tools"] == 17
    assert report["below_target"] == 0, [
        (row["name"], row["score"], row["problems"])
        for row in report["results"]
        if row["score"] < TARGET
    ]
    assert report["average"] >= TARGET, report["average"]


def test_every_router_has_an_action_enum_and_requires_it() -> None:
    for name, tool in _handler().advertised_tools().items():
        schema = tool["inputSchema"]
        action = schema["properties"]["action"]
        assert action["type"] == "string"
        assert len(action["enum"]) >= 1
        assert schema["required"] == ["action"]
        assert schema["additionalProperties"] is False
        assert name == tool["name"]


def test_read_routers_contain_only_read_only_originals() -> None:
    handler = _handler()
    for spec in ROUTER_SPECS.values():
        if spec.kind != "read":
            continue
        for action in spec.actions:
            if action.connector:
                continue
            annotations = handler.tools[action.original]["annotations"]
            assert annotations["readOnlyHint"] is True, (spec.name, action.action)
            assert annotations["destructiveHint"] is False, (spec.name, action.action)


def test_annotation_composites_are_truthful() -> None:
    handler = _handler()
    for spec in ROUTER_SPECS.values():
        annotations = handler.tools[spec.name]["annotations"]
        if spec.kind == "read":
            assert annotations["readOnlyHint"] is True
            assert annotations["destructiveHint"] is False
        else:
            assert annotations["readOnlyHint"] is False
            assert annotations["destructiveHint"] is True


def test_actions_are_unique_within_each_router() -> None:
    for spec in ROUTER_SPECS.values():
        actions = [action.action for action in spec.actions]
        assert len(actions) == len(set(actions)), spec.name


def test_routed_original_is_owned_by_one_router_family() -> None:
    """An original may appear once, except connector reads/writes (different verbs)."""
    seen: dict[str, str] = {}
    for spec in ROUTER_SPECS.values():
        for action in spec.actions:
            if action.connector or not action.original:
                continue
            if action.original in seen and not action.original.startswith("connector_"):
                raise AssertionError(
                    f"{action.original} is routed by both {seen[action.original]} and {spec.name}"
                )
            seen[action.original] = spec.name


def test_compact_surface_is_much_smaller_than_the_full_definition_set() -> None:
    compact = _handler()
    full = MCPProtocolHandler()
    compact_bytes = sum(len(json.dumps(tool)) for tool in compact.advertised_tools().values())
    full_bytes = sum(len(json.dumps(tool)) for tool in full.advertised_tools().values())
    assert compact_bytes < full_bytes * 0.5, (compact_bytes, full_bytes)


def test_descriptions_state_their_result() -> None:
    for name, tool in _handler().advertised_tools().items():
        description = tool.get("description") or ""
        assert len(description) >= 120, name
        assert "return" in description.lower(), name
