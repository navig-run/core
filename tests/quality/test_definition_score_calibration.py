"""Calibration guard for the definition scorer itself.

The scorer (`navig/mcp/definition_score.py`) is the instrument every before/after number in
`reports/quality/` is measured with. An instrument that drifts silently is worse than no
instrument, so these tests pin its behaviour on two fixtures whose verdicts are not
arguable:

* a complete, honest definition scores at or above the 4.5 target;
* the exact shape the published evaluation criticised — a read verb described as writing
  and annotated destructive — scores well below it.

They say nothing about the catalogue (that is `test_mcp_definition_budget.py` and
`tools/mcp_tdqs.py`); they say the ruler still measures.
"""

from __future__ import annotations

from navig.mcp.definition_score import score_catalogue, score_tool

GOOD = {
    "name": "navig_list_hosts",
    "title": "List hosts",
    "description": (
        "List every configured SSH host with its address, user and status. Read-only. Returns "
        "one row per host; use navig_host_info for one host's details and navig_list_apps when "
        "you need the apps behind them."
    ),
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {"filter": {"type": "string", "description": "Only hosts matching this pattern"}},
        "required": [],
        "additionalProperties": False,
    },
}

BAD = {
    "name": "connector_gmail_search",
    "title": "",
    "description": "Search Gmail. Search, read, send, and manage Gmail messages.",
    "annotations": {"readOnlyHint": False, "destructiveHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query"},
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return",
                "default": 5,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def test_a_complete_definition_reaches_the_target() -> None:
    row = score_tool(GOOD["name"], GOOD)
    assert row["score"] >= 4.5, row
    assert row["dimensions"] == {
        "identity": 1.0,
        "purpose": 1.0,
        "safety": 1.0,
        "routing": 1.0,
        "contract": 1.0,
    }


def test_the_evaluated_defect_scores_well_below_target() -> None:
    row = score_tool(BAD["name"], BAD)
    assert row["score"] <= 3.0, row
    assert "read tool advertises a write" in row["problems"]
    assert "read verb annotated destructive" in row["problems"]


def test_the_ruler_orders_them_and_averages() -> None:
    good = score_tool(GOOD["name"], GOOD)["score"]
    bad = score_tool(BAD["name"], BAD)["score"]
    assert good > bad
    summary = score_catalogue({GOOD["name"]: GOOD, BAD["name"]: BAD})
    assert summary["tools"] == 2
    assert summary["average"] == round((good + bad) / 2, 2)
    assert 0 < summary["average"] < 4.5
