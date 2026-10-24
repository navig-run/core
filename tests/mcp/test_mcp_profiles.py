"""The compact profile is the default surface; the full catalogue is unchanged.

The programmatic default (``MCPProtocolHandler()``) stays FULL so every existing test,
tool and integration keeps its contract; the CLI and ``python -m navig.mcp_server`` pass
``profile="default"``.
"""

from __future__ import annotations

import sys

from navig.mcp.routing import COMPACT_TOOL_NAMES
from navig.mcp_server import MCPProtocolHandler

FROZEN_FULL_ONLY = (
    "navig_run_command",
    "recall_ingest",
    "desktop_filesystem",
    "perplexity_ask",
    "navig_agent_status_get",
)


def test_programmatic_default_is_still_the_full_catalogue() -> None:
    handler = MCPProtocolHandler()
    assert len(handler.advertised_tools()) == len(handler.tools)
    assert "navig_list_hosts" in handler.advertised_tools()


def test_compact_profile_advertises_exactly_the_routers() -> None:
    handler = MCPProtocolHandler(profile="default")
    advertised = handler.advertised_tools()
    assert tuple(advertised) == COMPACT_TOOL_NAMES
    assert len(advertised) == 17
    assert {"navig_capabilities", "navig_run"} <= set(advertised)
    for name in advertised:
        assert name in handler._tool_handlers


def test_compact_still_registers_every_original_handler() -> None:
    full = MCPProtocolHandler()
    compact = MCPProtocolHandler(profile="default")
    missing = set(full._tool_handlers) - set(compact._tool_handlers)
    assert missing == set(), f"compact registration lost handlers: {sorted(missing)}"
    for name in FROZEN_FULL_ONLY:
        assert name in compact._tool_handlers, f"{name} must remain registered for --profile full"


def test_full_profile_contains_no_router_and_every_frozen_tool() -> None:
    full = MCPProtocolHandler()
    assert not set(COMPACT_TOOL_NAMES) & set(full.tools)
    for name in FROZEN_FULL_ONLY:
        assert name in full.advertised_tools()


def test_only_means_restricted_full_catalogue() -> None:
    handler = MCPProtocolHandler(only=["recall"])
    names = set(handler.advertised_tools())
    assert names and all(name.startswith("recall_") for name in names)
    assert not names & set(COMPACT_TOOL_NAMES)
    assert handler._profile == "full"


def test_resources_are_served_in_both_profiles() -> None:
    full = MCPProtocolHandler()
    compact = MCPProtocolHandler(profile="default")
    assert "navig://config/hosts" in full.resources
    assert "navig://config/hosts" in compact.resources


def test_compact_instructions_name_the_full_profile() -> None:
    compact = MCPProtocolHandler(profile="default")._handle_initialize({})
    full = MCPProtocolHandler()._handle_initialize({})
    assert "--profile full" in compact["instructions"]
    assert f"{len(COMPACT_TOOL_NAMES)} domain tools" in compact["instructions"]
    assert "navig_capabilities" in compact["instructions"]
    assert compact["instructions"] != full["instructions"]


def test_instructions_fall_back_to_static_text_when_the_digest_fails(monkeypatch) -> None:
    import navig.capabilities as caps

    def boom(*_a, **_k):
        raise RuntimeError("broken plugin")

    monkeypatch.setattr(caps, "digest", boom)
    text = MCPProtocolHandler(profile="default")._handle_initialize({})["instructions"]
    assert "--profile full" in text
    assert "Installed:" not in text


def test_capabilities_resource_is_served() -> None:
    compact = MCPProtocolHandler(profile="default")
    assert "navig://capabilities" in compact.resources


def test_linux_full_profile_excludes_windows_only_tools(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    handler = MCPProtocolHandler()
    names = set(handler.advertised_tools())
    windows_only = {
        name
        for name in names
        if name.startswith(("desktop_", "windows_")) and name != "desktop_filesystem"
    }
    assert windows_only == set()
    assert "desktop_filesystem" in names
    assert "navig_list_hosts" in names


def test_compact_profile_is_platform_stable(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    linux = set(MCPProtocolHandler(profile="default").advertised_tools())
    monkeypatch.setattr(sys, "platform", "win32")
    windows = set(MCPProtocolHandler(profile="default").advertised_tools())
    assert linux == windows == set(COMPACT_TOOL_NAMES)
