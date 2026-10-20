"""Definition budget for the MCP catalogue.

The published evaluation scored 120 tools at 3.4/5 and named two catalogue-wide gaps:
agents are not told *which sibling to use* and are not told *what comes back*. Measured on
this tree, 111 of 152 tools named no sibling tool and 88 stated no result.

Two guards, and they do different jobs:

* **per-tool** — the batch-1 tools were rewritten from their handlers, so they must keep
  naming a sibling AND stating their result. A regression here is a real regression, not a
  statistical wobble.
* **budget** — the whole catalogue may not get worse. The ceiling only goes DOWN: a new tool
  without routing has to be a deliberate decision (edit the ceiling in the same commit and
  say why), not drift grown one tool at a time.

Both read ``navig.mcp.catalogue_audit``, the same signals ``tools/mcp_inventory.py``
publishes, so the report and the gate cannot disagree.
"""

from __future__ import annotations

import pytest

from navig.mcp.catalogue_audit import SIBLING_TOOL, STATES_RESULT, counts, signals
from navig.mcp_server import MCPProtocolHandler

#: Ceilings as RATIOS, not absolute counts. The catalogue is not a fixed set: installed
#: plugins and connectors add tools (152 in the clean tree, 154 with the Perplexity
#: delegation plugin), so an absolute number is either too tight for one machine or
#: toothless for another. A ratio is the invariant: 71/152 = 0.467 and 72/154 = 0.468 are the
#: same figure.
#:
#: After batch 2 both ratios are ZERO: every advertised tool names a sibling and states its
#: result. The ceilings only move down; a new tool without routing or a result has to be a
#: deliberate decision made in the same commit as the ceiling change, not drift.
MAX_NO_SIBLING_RATIO = 0.0
MAX_NO_RESULT_RATIO = 0.0

#: Batch-1 tools: descriptions rebuilt from the handler in ``navig/mcp/annotations.py``.
#: ``navig_list_databases`` is deliberately absent — it has no sibling tool to route to, and
#: inventing one in prose would be a lie.
BATCH_ONE = frozenset(
    {
        "navig_search_wiki",
        "navig_read_wiki_page",
        "navig_list_wiki_pages",
        "navig_run_command",
        "navig_runtime_create_mission",
        "navig_runtime_list_receipts",
        "navig_agent_learning_run",
        "cdp_eval",
        "cdp_key",
        "cdp_move",
        "cdp_type",
        "cdp_navigate",
        "cdp_detach",
        "cdp_stop",
        "firecrawl_search",
        "memory_key_facts_forget",
        "memory_key_facts_update",
        "desktop_registry_set",
    }
)


@pytest.fixture(scope="module")
def handler() -> MCPProtocolHandler:
    return MCPProtocolHandler()


def test_every_advertised_tool_has_a_handler(handler: MCPProtocolHandler) -> None:
    """A definition with no dispatch handler is a tool that fails on first use."""
    missing = sorted(set(handler.tools) - set(getattr(handler, "_tool_handlers", {})))
    assert missing == [], f"advertised without a handler: {missing}"


def test_batch_one_tools_keep_routing_and_their_result_contract(handler: MCPProtocolHandler) -> None:
    broken: list[str] = []
    for name in sorted(BATCH_ONE):
        tool = handler.tools.get(name)
        assert tool is not None, f"{name} disappeared from the catalogue"
        description = tool.get("description") or ""
        if not SIBLING_TOOL.search(description):
            broken.append(f"{name}: names no sibling tool")
        if not STATES_RESULT.search(description):
            broken.append(f"{name}: states no result")
        if len(description) < 90:
            broken.append(f"{name}: too thin ({len(description)} chars)")
    assert broken == [], "batch-1 definitions regressed:\n" + "\n".join(broken)


def test_catalogue_stays_inside_the_definition_budget(handler: MCPProtocolHandler) -> None:
    totals = counts({name: signals(tool) for name, tool in handler.tools.items()})
    assert totals["undocumented_params"] == 0, f"{totals['undocumented_params']} tools hide a parameter"
    assert totals["non_strict_schema"] == 0, f"{totals['non_strict_schema']} tools accept unknown arguments"
    no_sibling_ratio = totals["no_sibling_hint"] / totals["tools"]
    no_result_ratio = totals["no_result_statement"] / totals["tools"]
    assert no_sibling_ratio <= MAX_NO_SIBLING_RATIO, (
        f"{totals['no_sibling_hint']}/{totals['tools']} tools give no routing hint "
        f"({no_sibling_ratio:.3f} > {MAX_NO_SIBLING_RATIO}) — lower the ceiling only by "
        "fixing definitions, never by raising it"
    )
    assert no_result_ratio <= MAX_NO_RESULT_RATIO, (
        f"{totals['no_result_statement']}/{totals['tools']} tools state no result "
        f"({no_result_ratio:.3f} > {MAX_NO_RESULT_RATIO})"
    )
