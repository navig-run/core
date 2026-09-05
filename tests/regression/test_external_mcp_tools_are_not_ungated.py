"""A tool NAVIG has never heard of must not be implicitly safe.

`is_destructive_tool` is an allowlist inverted into a denylist: it answers "yes" for
~45 of NAVIG's **own** tool names plus the `connector_*_act` shape, and "no" for
everything else. `gate_agent_tool_call` calls `needs_approval(tool_name)` with the
default ``safety_level="safe"``, so under the default CONFIRM_DESTRUCTIVE policy the
whole decision collapses to that membership test. A third-party MCP server publishing
``delete_all_repositories`` therefore ran with no confirmation and no audit line —
the gate did not *decide* it was safe, it simply had nothing to look it up in.

There are two doors, and they failed differently:

* **`navig.mcp.registry.MCPClientManager.call_tool`** — the live one, reachable from
  the gateway's ``POST /mcp/tools/{name}/call`` route. It consulted **no gate at all**;
  `navig.tools.approval` was not imported anywhere in `mcp/registry.py`,
  `mcp/client.py` or `gateway/routes/mcp.py`.
* **`navig.agent.mcp_client.MCPClientPool._register_tools`** — dormant today
  (`connect_all` has no production caller) but one call away, and it registered the
  wrapper under the server's **raw** tool name, so an upstream tool could also *shadow*
  a first-party one in the agent registry.

The fix is a shape rule in the shared reader (`needs_approval`), so it holds for any
name reaching any of the three call sites, plus a classification pushed in at discovery
so a server that honestly declares ``readOnlyHint: true`` still reads without a prompt.
Ported from Cloudflare OS's `packages/mcp-shared/src/tools.ts`, whose rule is that every
annotation test is ``is True``/``is False`` — an unannotated tool is an action.
"""

from __future__ import annotations

import pytest

from navig.tools.approval import (
    ApprovalDecision,
    ApprovalPolicy,
    get_approval_gate,
    needs_approval,
    set_approval_policy,
)


@pytest.fixture(autouse=True)
def _default_policy():
    """Every test here reasons about the DEFAULT policy."""
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)
    yield
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)


# ---------------------------------------------------------------------------
# The shared reader
# ---------------------------------------------------------------------------


def test_an_unannotated_external_tool_needs_approval() -> None:
    """The defect, at the one place all three call sites funnel through."""
    assert needs_approval("mcp__acme__delete_all_repositories") is True, (
        "An externally-defined tool name is not in DESTRUCTIVE_TOOLS, so the default "
        "CONFIRM_DESTRUCTIVE policy let it run unprompted. Externally-defined names "
        "must default to needing approval — see the shape rule beside "
        "_DESTRUCTIVE_NAME_SUFFIXES in navig/tools/approval.py."
    )


def test_a_first_party_read_tool_is_still_not_gated() -> None:
    """The reverse drift the sibling gate-parity test guards: don't over-gate reads.

    Gating a read costs nothing in safety and trains the operator to click through
    prompts, which is how a real prompt gets ignored.
    """
    assert needs_approval("read_file") is False


def test_a_vetted_read_only_tool_is_not_gated() -> None:
    """A server that honestly declares readOnlyHint, on an endpoint the operator
    vouched for, reads without a prompt — otherwise the classification buys nothing."""
    from navig.tools.approval import record_external_tool

    record_external_tool("mcp__acme__list_issues", mode="read", auto_approvable=False)
    assert needs_approval("mcp__acme__list_issues") is False


def test_a_recorded_action_is_still_gated() -> None:
    """Being classified is not the same as being permitted."""
    from navig.tools.approval import record_external_tool

    record_external_tool("mcp__acme__create_issue", mode="action", auto_approvable=False)
    assert needs_approval("mcp__acme__create_issue") is True


def test_forgetting_a_server_restores_deny_by_shape() -> None:
    """A disconnected server's downgrade must not outlive it."""
    from navig.tools.approval import forget_external_tools, record_external_tool

    record_external_tool("mcp__acme__list_issues", mode="read", auto_approvable=False)
    assert needs_approval("mcp__acme__list_issues") is False

    forget_external_tools("acme")
    assert needs_approval("mcp__acme__list_issues") is True, (
        "After the server is gone the name falls back to the shape rule, which denies. "
        "Fails closed twice: by shape, and by absence from the index."
    )


# ---------------------------------------------------------------------------
# Door 1 — the live one: MCPClientManager.call_tool
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal stand-in for MCPClient: connected, one tool, records the call."""

    def __init__(self, server_id: str, tool_name: str) -> None:
        from navig.mcp.client import MCPClientConfig
        from navig.mcp.protocol import MCPTool

        self.id = server_id
        self.is_connected = True
        self.config = MCPClientConfig(id=server_id, command="fake-server")
        self.called: list[tuple[str, dict]] = []
        self.tools = [
            MCPTool(
                name=tool_name,
                description="Irreversibly deletes everything.",
                input_schema={},
                server_id=server_id,
            )
        ]

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.called.append((name, arguments))
        return "DID THE THING"


async def test_manager_call_tool_is_gated(monkeypatch) -> None:
    """The live door: it consulted no gate at all, so a deny could not even be expressed."""
    from navig.mcp.registry import MCPClientManager

    async def _deny(_req) -> ApprovalDecision:
        return ApprovalDecision.DENIED

    get_approval_gate().backend = _deny

    mgr = MCPClientManager()
    client = _FakeClient("acme", "delete_all_repositories")
    mgr._clients["acme"] = client  # noqa: SLF001 — no public inject seam

    with pytest.raises(PermissionError):
        await mgr.call_tool("delete_all_repositories", {})

    assert client.called == [], (
        "The tool RAN despite the operator denying it. MCPClientManager.call_tool must "
        "consult the approval gate BEFORE dispatching to the client."
    )


async def test_manager_call_tool_proceeds_when_approved() -> None:
    """The gate must not become a wall: an approved call still runs."""
    from navig.mcp.registry import MCPClientManager

    async def _approve(_req) -> ApprovalDecision:
        return ApprovalDecision.APPROVED

    get_approval_gate().backend = _approve

    mgr = MCPClientManager()
    client = _FakeClient("acme", "delete_all_repositories")
    mgr._clients["acme"] = client  # noqa: SLF001

    result = await mgr.call_tool("delete_all_repositories", {"x": 1})

    assert result == "DID THE THING"
    assert client.called == [("delete_all_repositories", {"x": 1})]


async def test_ambiguous_tool_name_across_servers_is_refused() -> None:
    """`find_tool` was first-match-wins over a dict, so which server served a
    duplicated name depended on insertion order — a silent pick between two
    different remote systems."""
    from navig.mcp.registry import MCPClientManager

    async def _approve(_req) -> ApprovalDecision:
        return ApprovalDecision.APPROVED

    get_approval_gate().backend = _approve

    mgr = MCPClientManager()
    mgr._clients["acme"] = _FakeClient("acme", "deploy")  # noqa: SLF001
    mgr._clients["globex"] = _FakeClient("globex", "deploy")  # noqa: SLF001

    with pytest.raises(ValueError, match="ambiguous"):
        await mgr.call_tool("deploy", {})


# ---------------------------------------------------------------------------
# Scope — which tools a server may offer at all
# ---------------------------------------------------------------------------
#
# Trust says how far a server's own claims are believed; scope says which tools it may
# offer in the first place. Both are needed: a `vetted` server's declared reads run
# unprompted, so a tool it adds tomorrow is unprompted the day it appears.


async def test_a_tool_outside_the_configured_scope_is_refused(monkeypatch) -> None:
    from navig.mcp.registry import MCPClientManager

    monkeypatch.setattr(
        "navig.mcp.trust.allowed_tools_for_server", lambda _sid: ("safe_read",)
    )

    async def _approve(_req) -> ApprovalDecision:
        raise AssertionError("an out-of-scope tool must not even be offered for approval")

    get_approval_gate().backend = _approve

    mgr = MCPClientManager()
    client = _FakeClient("acme", "delete_all_repositories")
    mgr._clients["acme"] = client  # noqa: SLF001

    with pytest.raises(PermissionError, match="outside the configured scope"):
        await mgr.call_tool("delete_all_repositories", {})

    assert client.called == []


async def test_an_in_scope_tool_still_goes_through_the_gate(monkeypatch) -> None:
    """Scope is a narrowing, not a bypass — being allowed is not being approved."""
    from navig.mcp.registry import MCPClientManager

    monkeypatch.setattr(
        "navig.mcp.trust.allowed_tools_for_server",
        lambda _sid: ("delete_all_repositories",),
    )

    async def _deny(_req) -> ApprovalDecision:
        return ApprovalDecision.DENIED

    get_approval_gate().backend = _deny

    mgr = MCPClientManager()
    client = _FakeClient("acme", "delete_all_repositories")
    mgr._clients["acme"] = client  # noqa: SLF001

    with pytest.raises(PermissionError):
        await mgr.call_tool("delete_all_repositories", {})

    assert client.called == []


async def test_an_empty_scope_denies_rather_than_opens(monkeypatch) -> None:
    """A configured-but-empty allowlist is the dangerous case: collapsing it into
    'unrestricted' would turn a typo into full access."""
    from navig.mcp.registry import MCPClientManager

    monkeypatch.setattr("navig.mcp.trust.allowed_tools_for_server", lambda _sid: ())

    mgr = MCPClientManager()
    client = _FakeClient("acme", "anything")
    mgr._clients["acme"] = client  # noqa: SLF001

    with pytest.raises(PermissionError, match="outside the configured scope"):
        await mgr.call_tool("anything", {})


# ---------------------------------------------------------------------------
# One server's footprint on the prompt is bounded
# ---------------------------------------------------------------------------
#
# Every discovered tool becomes an entry in the agent's tool schema, sent to the model
# on EVERY request. A server advertising thousands of tools inflates the prompt — and
# the bill — without ever being called, and nothing in the protocol stops it.


async def test_a_server_cannot_contribute_unbounded_tools() -> None:
    from navig.mcp.client import MCPClient, MCPClientConfig
    from navig.mcp.trust import MAX_TOOLS_PER_SERVER

    client = MCPClient(MCPClientConfig(id="flood", command="x"))
    capped = client._cap_tools([{"name": f"t{i}"} for i in range(MAX_TOOLS_PER_SERVER * 3)])  # noqa: SLF001

    assert len(capped) == MAX_TOOLS_PER_SERVER


async def test_a_normal_catalog_is_untouched() -> None:
    from navig.mcp.client import MCPClient, MCPClientConfig

    client = MCPClient(MCPClientConfig(id="ok", command="x"))
    tools = [{"name": f"t{i}"} for i in range(12)]

    assert client._cap_tools(tools) is tools  # noqa: SLF001


async def test_the_cut_is_not_silent(monkeypatch) -> None:
    """'200 tools' and '200 of 4000 tools' are different situations; a truncation that
    does not say so reads as complete coverage.

    Captures the module logger directly rather than via `caplog`: navig loggers set
    `propagate=False`, so caplog sees nothing and the assertion would pass vacuously.
    """
    import navig.mcp.client as mod
    from navig.mcp.client import MCPClient, MCPClientConfig
    from navig.mcp.trust import MAX_TOOLS_PER_SERVER

    warnings: list[tuple] = []
    monkeypatch.setattr(
        mod.logger, "warning", lambda msg, *a, **k: warnings.append((msg, a))
    )

    client = MCPClient(MCPClientConfig(id="flood", command="x"))
    client._cap_tools([{"name": f"t{i}"} for i in range(MAX_TOOLS_PER_SERVER + 5)])  # noqa: SLF001

    assert warnings, "the catalog was truncated silently"
    msg, args = warnings[0]
    assert "advertised" in msg
    # the real numbers, not just the fact that something happened
    assert MAX_TOOLS_PER_SERVER + 5 in args and MAX_TOOLS_PER_SERVER in args
