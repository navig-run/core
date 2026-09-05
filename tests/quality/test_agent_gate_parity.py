"""Two gates, two registries — they must agree on what is dangerous.

NAVIG asks "may this tool run?" in two places, and until now they consulted different
lists:

* the **MCP server** — `mcp_server._gate_tool` reads `server._tool_safety`, the
  per-bundle classification;
* the **agent loop** — `gate_agent_tool_call` → `needs_approval` reads
  `approval.DESTRUCTIVE_TOOLS`, a name set.

#759 classified 34 MCP tools as "dangerous" for the first gate and left the second
untouched. Measured afterwards: **29 of those 34 were absent from `DESTRUCTIVE_TOOLS`**,
so an agent calling `desktop_powershell` — "Execute a PowerShell command or script on the
local machine" — was not gated at all, while the same tool over MCP was. `needs_approval`
returned False for `desktop_powershell` and `navig_run_command`, and True for `cdp_eval`:
whoever added the cdp tools updated both registries, and I updated only one.

Duplicating names across two registries is what produced the drift, so this test is the
thing that makes duplication safe: the lists may live apart, but they cannot disagree.

Connector write tools are generated per installed connector and cannot appear in a static
set, so `is_destructive_tool` matches them by shape (`connector_*_act`) and this test
checks that path rather than demanding enumeration.
"""

from __future__ import annotations

import functools
from unittest.mock import MagicMock

import pytest

from navig.tools.approval import (
    DESTRUCTIVE_TOOLS,
    is_destructive_tool,
    needs_approval,
)


@functools.lru_cache(maxsize=1)
def _mcp_levels() -> tuple[tuple[str, str], ...]:
    """The MCP classification, as (tool, level) pairs. Cached; registration is ~0.3s."""
    from navig.mcp.tools import register_all_tools

    server = MagicMock()
    server.tools = {}
    server._tool_handlers = {}
    del server._tool_safety
    register_all_tools(server)
    return tuple(sorted(server._tool_safety.items()))


def _dangerous() -> list[str]:
    return [name for name, level in _mcp_levels() if level == "dangerous"]


def test_every_dangerous_mcp_tool_is_destructive_for_the_agent() -> None:
    """The gap #759 left: classified dangerous on one path, ungated on the other."""
    missing = sorted(t for t in _dangerous() if not is_destructive_tool(t))

    assert not missing, (
        "These tools are classified \"dangerous\" for the MCP gate but are not treated as "
        "destructive by the agent gate, so `gate_agent_tool_call` lets the agent run them "
        "with no confirmation and no audit line. Add each to `DESTRUCTIVE_TOOLS` in "
        "navig/tools/approval.py (or, for a generated name, to the shape rules beside "
        "it):\n" + "\n".join(f"  {m}" for m in missing)
    )


def test_the_agent_gate_actually_holds_them() -> None:
    """Same property through the real predicate the agent loop calls."""
    ungated = sorted(t for t in _dangerous() if not needs_approval(t))

    assert not ungated, (
        "`needs_approval` returns False for these dangerous tools under the default "
        f"CONFIRM_DESTRUCTIVE policy:\n" + "\n".join(f"  {u}" for u in ungated)
    )


def test_no_safe_mcp_tool_is_treated_as_destructive() -> None:
    """The reverse drift: a read-only tool must not start demanding approval.

    Gating a read costs nothing in safety and trains the operator to click through
    prompts, which is how a real prompt gets ignored.
    """
    safe = [name for name, level in _mcp_levels() if level == "safe"]
    wrongly_gated = sorted(t for t in safe if is_destructive_tool(t))

    assert not wrongly_gated, (
        "These tools are classified \"safe\" but the agent gate treats them as "
        f"destructive:\n" + "\n".join(f"  {w}" for w in wrongly_gated)
    )


@pytest.mark.parametrize(
    "tool",
    [
        "desktop_powershell",
        "navig_run_command",
        "desktop_process_kill",
        "desktop_registry_set",
        "desktop_registry_delete",
        "desktop_ahk",
        "desktop_filesystem",
        "navig_block_apply",
        "cdp_eval",
    ],
)
def test_named_high_risk_tools_stay_gated_on_the_agent_path(tool: str) -> None:
    """Name the ones that matter, so a refactor cannot quietly drop one."""
    assert is_destructive_tool(tool), f"{tool} must be destructive for the agent gate"
    assert needs_approval(tool), f"{tool} must require approval under the default policy"


def test_generated_connector_write_tools_are_destructive_by_shape() -> None:
    """`connector_{id}_act` cannot be enumerated — the set depends on the install."""
    assert is_destructive_tool("connector_gmail_act")
    assert is_destructive_tool("connector_a_brand_new_service_act")  # future connector
    # reads are not destructive
    assert not is_destructive_tool("connector_gmail_search")
    assert not is_destructive_tool("connector_gmail_fetch")


def test_the_static_set_has_no_stale_mcp_entries() -> None:
    """An entry naming an MCP tool that is no longer dangerous is a stale claim.

    Only checked for names the MCP registry knows about — the set also covers agent-side
    tools (`bash_exec`, `write_file`, …) that never appear there.
    """
    levels = dict(_mcp_levels())
    stale = sorted(
        name
        for name in DESTRUCTIVE_TOOLS
        if name in levels and levels[name] != "dangerous"
    )

    assert not stale, (
        "These are listed as destructive for the agent gate but are no longer classified "
        f"dangerous by their MCP bundle — reconcile the two:\n"
        + "\n".join(f"  {s} (now {levels[s]!r})" for s in stale)
    )
