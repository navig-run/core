"""The catalogue has to stay findable, not merely complete.

The selection benchmark (``navig.mcp.selection_benchmark``) asks 40 realistic requests and
ranks the catalogue on the text a client actually receives. Two properties are gated here:

* every expected tool exists on this platform (a rename or a dropped bundle would otherwise
  turn a benchmark into a silent no-op);
* the floor does not drop. Measured on this tree: top-1 0.80 / top-3 0.95 against the
  installed 3.27.1 baseline's 0.775 / 0.875 — the same evaluator, different catalogue
  (152 vs 154 tools), so the comparison is like-for-like on the requests and not on the
  tool set.

The floor is a ratchet: description work raises it, nothing lowers it. A miss is not
necessarily a defect — a lexical ranker reads words, not intent — which is why top-3 is
gated too: an agent that sees three candidates can still choose well.
"""

from __future__ import annotations

import pytest

from navig.mcp.selection_benchmark import BENCHMARK, run
from navig.mcp_server import MCPProtocolHandler

#: Measured after batch 1/3 (before: 0.775 / 0.875 on the installed release).
FLOOR_ACCEPTABLE_TOP1 = 0.75
FLOOR_TOP3 = 0.90


@pytest.fixture(scope="module")
def catalogue() -> dict[str, dict]:
    return MCPProtocolHandler().tools


def test_every_benchmark_request_names_a_tool_this_catalogue_serves(catalogue: dict[str, dict]) -> None:
    missing = sorted(
        {name for case in BENCHMARK for name in case["expect"]} - set(catalogue)
    )
    assert missing == [], f"benchmark expects tools this catalogue does not serve: {missing}"


def test_the_catalogue_stays_findable(catalogue: dict[str, dict]) -> None:
    report = run(catalogue)
    assert report["scored"] == report["requests"], "a benchmark request was skipped — see the other test"
    assert report["accuracy_acceptable_top1"] >= FLOOR_ACCEPTABLE_TOP1, (
        f"top-1 {report['accuracy_acceptable_top1']} fell below {FLOOR_ACCEPTABLE_TOP1} "
        f"over {report['scored']} requests"
    )
    assert report["accuracy_top3"] >= FLOOR_TOP3, (
        f"top-3 {report['accuracy_top3']} fell below {FLOOR_TOP3} over {report['scored']} requests"
    )
