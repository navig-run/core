"""Read verbs must be annotated as reads, not only classified safely by the gate.

These two signals are separate and both matter:

* ``_tool_safety`` decides whether a call RUNS (safe → ungated, moderate/dangerous → the
  approval gate);
* ``readOnlyHint`` / ``destructiveHint`` decide what a CLIENT tells the operator and whether
  an agent may repeat the call freely.

A tool classified ``safe`` by the gate but annotated ``destructiveHint: true`` is the
contradiction the definition scorer penalises (`navig_space_read` scored 2.8, `dock_status`
3.2 before this): the server promises an operation may destroy something, then never asks.
The reverse — an annotated read-only tool gated ``dangerous`` — is guarded for connectors in
``test_mcp_connector_metadata.py``.

Every tool named here reads Spaces metadata or the Dock's browser state; the comment beside
each group in ``annotations.py`` says why the two capture tools and the screenshot writer are
deliberately NOT read-only.
"""

from __future__ import annotations

import pytest

from navig.mcp_server import MCPProtocolHandler

#: Pure reads that must never be advertised as destructive.
READ_ONLY = (
    "navig_space_current",
    "navig_space_related",
    "navig_space_context",
    "navig_space_read",
    "dock_status",
    "dock_tabs",
    "dock_text",
    "dock_snapshot",
)

#: Reads with a real side effect — they must STAY destructive.
NOT_READ_ONLY = (
    "dock_console",  # first call starts capturing; `clear` empties the buffer
    "dock_network",  # same
    "dock_screenshot",  # writes a PNG to disk
)


@pytest.fixture(scope="module")
def tools() -> dict[str, dict]:
    return MCPProtocolHandler().tools


@pytest.mark.parametrize("name", READ_ONLY)
def test_a_read_is_annotated_as_a_read(name: str, tools: dict[str, dict]) -> None:
    tool = tools[name]
    hints = tool["annotations"]
    assert hints["readOnlyHint"] is True, f"{name} reads but is advertised as a write"
    assert hints["destructiveHint"] is False, f"{name} reads but is advertised as destructive"


@pytest.mark.parametrize("name", NOT_READ_ONLY)
def test_a_side_effect_keeps_its_warning(name: str, tools: dict[str, dict]) -> None:
    assert tools[name]["annotations"]["readOnlyHint"] is False, (
        f"{name} has a side effect; calling it read-only would let an agent repeat it freely"
    )
