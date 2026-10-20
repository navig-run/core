"""Selection floor for the compact surface.

Measured baseline on this tree: tool top-1 0.891, action top-1 0.766, tool top-3 0.969.
The floor is set with margin so definition edits that confuse routing fail the gate, while
a genuinely ambiguous phrase (e.g. "open record" vs "search") is not treated as a defect.
"""

from __future__ import annotations

from navig.mcp.router_benchmark import BENCHMARK, run
from navig.mcp.routing import ROUTER_SPECS

FLOOR_TOOL_TOP1 = 0.85
FLOOR_ACTION_TOP1 = 0.72
FLOOR_TOOL_TOP3 = 0.90


def test_every_benchmark_action_exists_in_the_contract() -> None:
    for case in BENCHMARK:
        spec = ROUTER_SPECS.get(case["tool"])
        assert spec is not None, case
        assert case["action"] in {action.action for action in spec.actions}, case


def test_selection_meets_the_floor() -> None:
    report = run()
    assert report["accuracy_tool_top1"] >= FLOOR_TOOL_TOP1, report["accuracy_tool_top1"]
    assert report["accuracy_action_top1"] >= FLOOR_ACTION_TOP1, report["accuracy_action_top1"]
    assert report["accuracy_tool_top3"] >= FLOOR_TOOL_TOP3, report["accuracy_tool_top3"]


def test_every_request_produces_a_prediction() -> None:
    report = run()
    assert all(row["picked"] for row in report["results"])
