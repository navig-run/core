"""Clearing the connector registry must not strand the process connector-less.

`bootstrap.ensure_connectors_loaded()` is guarded by a module flag, and its own docstring
describes the failure it was written to prevent: a flag stuck at True means "every later
call returned immediately and the process stayed permanently connector-less, with no way
to retry".

That failure came back from the other direction. `ConnectorRegistry.reset()` emptied the
registrations and left the flag alone, so the two disagreed — registry empty, flag saying
loaded — and nothing could reload. Measured before the fix, after running
`tests/connectors/test_connectors_bootstrap.py` in a session that had already loaded:

    BEFORE  flag=True  registry=16
    AFTER   flag=True  registry=0     <- inconsistent
    RELOAD  flag=True  registry=0     <- unrecoverable

The visible damage was in a *different* directory, which is why it read as flake: every
connector-derived MCP tool disappeared, so
`tests/quality/test_mcp_tool_safety_coverage.py::test_connector_write_tools_are_dangerous`
and `::test_connector_reads_are_not_safe` failed with "no connector tools were registered —
the check would be vacuous", while passing in isolation. Two safety guards asserting nothing.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def loaded_registry():
    """A loaded registry, restored to loaded state afterwards."""
    from navig.connectors.bootstrap import ensure_connectors_loaded
    from navig.connectors.registry import get_connector_registry

    ensure_connectors_loaded()
    registry = get_connector_registry()
    if not registry.all_classes():
        pytest.skip("no builtin connectors available in this environment")
    yield registry
    registry.reset()
    ensure_connectors_loaded()


def test_reset_leaves_the_loaded_flag_consistent(loaded_registry):
    """The flag must not keep claiming 'loaded' over an empty registry."""
    import navig.connectors.bootstrap as bootstrap

    loaded_registry.reset()

    assert loaded_registry.all_classes() == {}
    assert bootstrap._CONNECTORS_LOADED is False, (
        "the registry was emptied but the bootstrap still claims connectors are loaded — "
        "ensure_connectors_loaded() will return immediately and nothing can reload them"
    )


def test_connectors_reload_after_a_reset(loaded_registry):
    """The recovery the flag was blocking: a reset must be survivable."""
    from navig.connectors.bootstrap import ensure_connectors_loaded

    before = set(loaded_registry.all_classes())
    loaded_registry.reset()
    ensure_connectors_loaded()

    assert set(loaded_registry.all_classes()) == before, (
        "connectors did not come back after a registry reset — the process is stranded "
        "connector-less, which silently empties every connector-derived MCP tool"
    )


def test_the_mcp_connector_tools_survive_a_reset(loaded_registry):
    """The surface that actually broke: connector-derived MCP tools.

    Asserted end-to-end rather than via the flag, because the flag is the mechanism and
    this is the consequence — two coverage guards were asserting over an empty dict.
    """
    from unittest.mock import MagicMock

    from navig.mcp.tools import register_all_tools

    def _connector_tools() -> set[str]:
        server = MagicMock()
        server.tools = {}
        server._tool_handlers = {}
        del server._tool_safety
        register_all_tools(server)
        return {n for n in server._tool_safety if n.startswith("connector_")}

    assert _connector_tools(), "no connector MCP tools registered even before a reset"

    loaded_registry.reset()

    assert _connector_tools(), (
        "connector MCP tools vanished after a registry reset — the state that made "
        "test_connector_write_tools_are_dangerous vacuous"
    )
