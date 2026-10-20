"""The definition target is a gate, not a dashboard.

Batch 1 fixed the connector generator and 19 hand-written descriptions; batch 2 completed
the rest of the catalogue with per-tool routing/result hints (``annotations.DESCRIPTION_HINTS``)
and closed the 4.5 gap. This file is what stops the catalogue sliding back:

* the same scorer that produced ``reports/quality/tdqs-evaluation.json`` must keep every
  advertised tool at or above the 4.5 target (so the average is guaranteed, not sampled);
* no definition may carry a contradiction the scorer names (a read that advertises a write,
  a read annotated destructive, a write annotated non-destructive);
* every generated connector write verb must point at a read sibling that actually exists for
  that provider — the generator's routing is capability-aware, so it cannot route a
  search-only connector at a fetch tool it does not serve;
* every key in ``DESCRIPTION_HINTS`` must name a tool in the catalogue (a dead key silently
  never applies — the ``recall_tables`` trap documented in ``annotations.py``);
* applying the metadata pass twice must not change a description (idempotence).

The scorer is our calibrated local instrument, not the unreleased published evaluator — see
``definition_score.py`` and ``reports/quality/tdqs.md`` for what that does and does not mean.
"""

from __future__ import annotations

import sys

import pytest

from navig.mcp.annotations import DESCRIPTION_HINTS, annotate_tools
from navig.mcp.definition_score import score_catalogue
from navig.mcp_server import MCPProtocolHandler

TARGET = 4.5

#: Problems the scorer emits that mean the definition contradicts itself, whatever the score.
CONTRADICTIONS = (
    "read tool advertises a write",
    "read verb annotated destructive",
    "write verb not annotated destructive",
)


@pytest.fixture(scope="module")
def handler() -> MCPProtocolHandler:
    return MCPProtocolHandler()


@pytest.fixture(scope="module")
def catalogue(handler: MCPProtocolHandler) -> dict:
    return score_catalogue(handler.tools)


def test_every_advertised_tool_meets_the_definition_target(catalogue: dict) -> None:
    below = [row for row in catalogue["results"] if row["score"] < TARGET]
    assert below == [], (
        f"{len(below)} tool(s) below the {TARGET} target: "
        + ", ".join(f"{row['name']}={row['score']}" for row in below)
    )
    assert catalogue["average"] >= TARGET, catalogue["average"]


def test_no_definition_carries_a_safety_contradiction(catalogue: dict) -> None:
    broken = [
        f"{row['name']}: {problem}"
        for row in catalogue["results"]
        for problem in row["problems"]
        if problem in CONTRADICTIONS
    ]
    assert broken == [], "definition contradiction(s):\n" + "\n".join(broken)


def test_connector_write_verbs_route_at_a_sibling_that_exists(handler: MCPProtocolHandler) -> None:
    """The capability-aware generator may not name a read the provider does not serve.

    A search-only connector cannot send the caller to its ``_fetch`` tool, because the
    catalogue does not advertise one. This is the failure mode that made the generator
    append one blurb to every verb in the first place, so it is asserted against the served
    catalogue rather than the manifest.
    """
    names = set(handler.tools)
    broken: list[str] = []
    for name, tool in sorted(handler.tools.items()):
        if not name.startswith("connector_") or not name.endswith("_act"):
            continue
        description = tool.get("description") or ""
        siblings = [
            other
            for other in names
            if other.startswith(f"connector_{name[len('connector_'):-len('_act')]}_")
            and other != name
        ]
        if siblings and not any(sibling in description for sibling in siblings):
            broken.append(f"{name}: names none of {sorted(siblings)}")
    assert broken == [], "connector write verbs with no real read sibling:\n" + "\n".join(broken)


def test_every_hint_names_a_tool_that_is_advertised(handler: MCPProtocolHandler) -> None:
    """A hint keyed to a tool that is not served is dead config, not documentation."""
    missing = sorted(set(DESCRIPTION_HINTS) - set(handler.tools))
    if sys.platform != "win32":
        # Windows UI-automation bundles are (correctly) not advertised on other platforms;
        # their hints still matter on Windows, so they are exempt rather than deleted.
        missing = [name for name in missing if not name.startswith("desktop_")]
    assert missing == [], f"DESCRIPTION_HINTS keys with no tool in the catalogue: {missing}"


def test_annotation_pass_is_idempotent() -> None:
    """Running the metadata pass twice must not accumulate prose."""
    tools = {
        "navig_list_hosts": {
            "name": "navig_list_hosts",
            "description": "List all configured SSH hosts in NAVIG.",
            "inputSchema": {"type": "object", "properties": {}},
        }
    }
    annotate_tools(tools)
    first = tools["navig_list_hosts"]["description"]
    annotate_tools(tools)
    assert tools["navig_list_hosts"]["description"] == first
