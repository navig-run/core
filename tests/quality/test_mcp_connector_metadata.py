"""Generated connector tools must describe — and be annotated for — exactly what the
provider exposes.

The published tool-definition evaluation (v0.1.0, 2026-10-19, 120 tools, average 3.4/5)
scored the whole ``connector_*`` family worst (2.2-2.8/5), and both complaints were
contradictions a client cannot resolve:

* a search tool annotated ``readOnlyHint: false`` + ``destructiveHint: true``, so an agent
  was told that a pure read might destroy something;
* a search tool described as "Search, read, send, and manage Gmail messages", because the
  generator appended the CONNECTOR's blurb to every verb it emitted — advertising writes
  the schema cannot reach.

Both defects came from one place, ``navig/mcp/tools/connectors.py``, so these tests guard
the GENERATOR rather than the ~40 tools it emits: a connector added tomorrow inherits the
same guarantees without anyone editing this file.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from navig.connectors.bootstrap import ensure_connectors_loaded
from navig.connectors.registry import get_connector_registry
from navig.connectors.types import ActionType
from navig.mcp.annotations import annotate_tools, connector_annotations
from navig.mcp.tools import connectors as bridge

#: Verbs that claim a write. They may appear in an ``_act`` description — never in the
#: description of a ``_search``/``_fetch`` tool, whose schema has no way to perform them.
#: "change" is deliberately absent: a read description says "nothing is changed".
WRITE_CLAIM = re.compile(
    r"\b(send|send[s]?|manage|manages|delete|deletes|update|updates|create|creates|"
    r"post|posts|write|writes|archive|archives|label|labels|move|moves|reply|replies)\b",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def catalogue():
    """``(tools, manifests)`` for every currently registered connector."""
    ensure_connectors_loaded()
    registry = get_connector_registry()
    tools = {tool["name"]: tool for tool in bridge.list_connector_tools(registry)}
    manifests = {cid: cls.manifest for cid, cls in registry.all_classes().items()}
    return tools, manifests


def _operation(name: str) -> str:
    return name.rsplit("_", 1)[-1]


def _connector_id(name: str) -> str:
    operation = _operation(name)
    return name[len("connector_") : -(len(operation) + 1)]


def _generated(tools: dict[str, dict]) -> dict[str, dict]:
    """Only the per-provider tools (``connector_list``/``connector_health`` are meta)."""
    return {name: tool for name, tool in tools.items() if _operation(name) in {"search", "fetch", "act"}}


def test_the_catalogue_actually_has_generated_connector_tools(catalogue):
    tools, _ = catalogue
    generated = _generated(tools)
    # A fixture that silently produced an empty catalogue would make every test below
    # vacuous, which is worse than a red test.
    assert len(generated) >= 20, f"only {len(generated)} connector tools generated"
    assert {"connector_list", "connector_health"} <= set(tools)


def test_every_generated_tool_is_a_complete_definition(catalogue):
    tools, manifests = catalogue
    for name, tool in _generated(tools).items():
        cid = _connector_id(name)
        display = manifests[cid].display_name
        assert tool.get("title"), f"{name}: no title"
        assert display in tool["title"], f"{name}: title {tool['title']!r} does not name {display}"
        description = tool.get("description") or ""
        assert len(description) >= 80, f"{name}: description too thin ({len(description)} chars)"
        assert tool.get("annotations"), f"{name}: no annotations"
        schema = tool.get("inputSchema")
        assert isinstance(schema, dict) and schema.get("type") == "object", f"{name}: bad schema"


def test_read_verbs_are_read_only_and_never_destructive(catalogue):
    """The exact contradiction the evaluation flagged on 15 tools."""
    tools, _ = catalogue
    for name, tool in _generated(tools).items():
        if _operation(name) not in {"search", "fetch"}:
            continue
        hints = tool["annotations"]
        assert hints["readOnlyHint"] is True, f"{name}: a read is advertised as a write"
        assert hints["destructiveHint"] is False, f"{name}: a read is advertised as destructive"
        assert hints["openWorldHint"] is True, f"{name}: a connector call leaves the machine"
        assert hints["idempotentHint"] is True, f"{name}: repeating a read changes nothing"


def test_the_write_verb_is_destructive_and_not_read_only(catalogue):
    tools, _ = catalogue
    for name, tool in _generated(tools).items():
        if _operation(name) != "act":
            continue
        hints = tool["annotations"]
        assert hints["readOnlyHint"] is False, f"{name}: a write is advertised as read-only"
        assert hints["destructiveHint"] is True, f"{name}: a write must warn a client"
        assert hints["openWorldHint"] is True, f"{name}: a connector call leaves the machine"
        assert "idempotentHint" not in hints, f"{name}: a write must not claim idempotence"


def test_read_descriptions_advertise_no_write_actions(catalogue):
    tools, _ = catalogue
    offenders = []
    for name, tool in _generated(tools).items():
        if _operation(name) not in {"search", "fetch"}:
            continue
        match = WRITE_CLAIM.search(tool["description"])
        if match:
            offenders.append(f"{name}: claims {match.group(0)!r}")
    assert offenders == [], "read tools advertise writes they cannot perform: " + "; ".join(offenders)


def test_search_routes_to_fetch_only_when_the_provider_has_one(catalogue):
    tools, manifests = catalogue
    for name, tool in _generated(tools).items():
        if _operation(name) != "search":
            continue
        cid = _connector_id(name)
        fetch_tool = f"connector_{cid}_fetch"
        mentions_fetch = fetch_tool in tool["description"]
        if manifests[cid].can_fetch:
            assert mentions_fetch, f"{name}: does not point at {fetch_tool} for full content"
        else:
            assert not mentions_fetch, f"{name}: points at {fetch_tool}, which is not generated"


def test_act_schema_pins_action_type_to_the_enum(catalogue):
    tools, _ = catalogue
    for name, tool in _generated(tools).items():
        if _operation(name) != "act":
            continue
        schema = tool["inputSchema"]
        assert schema["properties"]["action_type"]["enum"] == [action.value for action in ActionType]
        assert schema["properties"]["resource_id"].get("minLength") == 1
        assert schema["required"] == ["action_type"]


def test_search_limit_bound_is_enforced_by_the_handler():
    """The schema says 1-50, so the handler must not forward 10 000 to the provider."""
    limits: list[int] = []

    class _CapturingConnector:
        class manifest:  # noqa: N801 - duck-typed stand-in for ConnectorManifest
            requires_oauth = False

        async def search(self, query: str, limit: int = 5):
            limits.append(limit)
            return []

    connector = _CapturingConnector()

    class _StubRegistry:
        def get(self, connector_id: str):
            assert connector_id == "fake"
            return connector

    for asked, expected in ((10_000, 50), (0, 1), (-3, 1), (7, 7), ("9", 9), (None, 5)):
        asyncio.run(
            bridge.handle_connector_call(
                "connector_fake_search",
                {"query": "q", "limit": asked},
                registry=_StubRegistry(),
            )
        )
        assert limits[-1] == expected, f"limit={asked!r} reached the provider as {limits[-1]}"


def test_annotations_are_never_friendlier_than_the_approval_gate():
    """Client-facing hints and the gate must agree — the gate is the one that decides.

    A tool may be MORE cautious in its annotation than in the gate (a moderate read can
    honestly say ``destructiveHint: false``), never less: nothing annotated read-only may
    be gated as dangerous, and nothing gated dangerous may be announced as harmless.
    """

    class _StubServer:
        def __init__(self):
            self.tools: dict[str, dict] = {}
            self._tool_handlers: dict[str, object] = {}

    server = _StubServer()
    bridge.register(server)
    # The real pipeline applies blanks after every bundle registers.
    annotate_tools(server.tools, server._tool_safety)

    for name, tool in server.tools.items():
        safety = server._tool_safety.get(name)
        assert safety is not None, f"{name} has no gate classification — it would run ungated"
        hints = tool["annotations"]
        if safety == "dangerous":
            assert hints["destructiveHint"] is True, f"{name}: dangerous but advertised harmless"
            assert hints["readOnlyHint"] is False, f"{name}: dangerous but advertised read-only"
        if hints["readOnlyHint"]:
            assert safety != "dangerous", f"{name}: advertised read-only but gated dangerous"
        operation = _operation(name)
        if operation in {"search", "fetch", "act"}:
            assert hints == connector_annotations(operation), (
                f"{name}: annotations drifted from connector_annotations({operation!r})"
            )
