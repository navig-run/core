"""An action runs unprompted only when the server AND the operator both allow it.

Holding every third-party write for a prompt is correct and, on its own, unusable: an
operator who is prompted for the same idempotent call fifty times reaches for `--yes`,
and that switches off every gate at once. The pressure is the vulnerability.

So there is a release valve, and it needs two independent parties because either alone
is the wrong authority:

* **Gate 1 — the server**, via `ClassifiedTool.auto_approvable`: a *vetted* endpoint
  declaring the tool `destructiveHint: false` AND `idempotentHint: true`. A `byo`
  server cannot produce this no matter what it annotates, so a stranger cannot mark
  their own homework.
* **Gate 2 — the operator**, via `mcp.trust.servers.<id>.auto_approve`: they named the
  tool. Without gate 1 this would be a blank cheque written against a description they
  cannot see change.

Both false is the default. `auto_approvable` had been *recorded and never read* since it
was introduced — this is the code that consumes it, and these are the tests that stop it
being released by one party.
"""

from __future__ import annotations

import pytest

from navig.mcp.trust import ServerTrust, classify_tool
from navig.tools.approval import (
    ApprovalPolicy,
    external_tool_was_pre_authorised,
    needs_approval,
    record_external_tool,
    set_approval_policy,
)

VETTED_IDEMPOTENT = {"destructiveHint": False, "idempotentHint": True}


@pytest.fixture(autouse=True)
def _default_policy():
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)
    yield
    set_approval_policy(ApprovalPolicy.CONFIRM_DESTRUCTIVE)


def _record(*, auto_approvable: bool, pre_authorised: bool, mode: str = "action") -> str:
    name = "mcp__acme__sync_issue"
    record_external_tool(
        name,
        mode=mode,
        auto_approvable=auto_approvable,
        operator_pre_authorised=pre_authorised,
        server="acme",
    )
    return name


# ---------------------------------------------------------------------------
# Both gates, or no release
# ---------------------------------------------------------------------------


def test_both_gates_release_the_action() -> None:
    name = _record(auto_approvable=True, pre_authorised=True)

    assert needs_approval(name) is False
    assert external_tool_was_pre_authorised(name) is True


def test_the_server_alone_does_not_release_it() -> None:
    """Gate 1 without gate 2 is the server marking its own homework."""
    name = _record(auto_approvable=True, pre_authorised=False)

    assert needs_approval(name) is True
    assert external_tool_was_pre_authorised(name) is False


def test_the_operator_alone_does_not_release_it() -> None:
    """Gate 2 without gate 1 is a blank cheque: the operator pre-authorised a tool the
    server never claimed was idempotent, and its description can change underneath."""
    name = _record(auto_approvable=False, pre_authorised=True)

    assert needs_approval(name) is True
    assert external_tool_was_pre_authorised(name) is False


def test_neither_gate_is_the_default() -> None:
    name = _record(auto_approvable=False, pre_authorised=False)

    assert needs_approval(name) is True


def test_an_unclassified_tool_is_never_released() -> None:
    """No record at all still fails closed — the shape rule does not care about gates."""
    assert needs_approval("mcp__acme__never_seen") is True
    assert external_tool_was_pre_authorised("mcp__acme__never_seen") is False


# ---------------------------------------------------------------------------
# Gate 1 can only come from a vetted endpoint
# ---------------------------------------------------------------------------


def test_a_byo_server_cannot_produce_gate_1() -> None:
    """The tier is the deployment's word, not the server's — so a stranger who
    annotates perfectly still cannot reach auto-approval."""
    classified = classify_tool(
        "sync_issue",
        server="acme",
        annotations=VETTED_IDEMPOTENT,
        trust=ServerTrust.BYO,
    )
    assert classified.auto_approvable is False

    record_external_tool(
        classified.registry_name,
        mode=classified.mode.value,
        auto_approvable=classified.auto_approvable,
        operator_pre_authorised=True,  # operator said yes; it still asks
        server="acme",
    )
    assert needs_approval(classified.registry_name) is True


def test_a_vetted_server_produces_gate_1() -> None:
    classified = classify_tool(
        "sync_issue",
        server="acme",
        annotations=VETTED_IDEMPOTENT,
        trust=ServerTrust.VETTED,
    )
    assert classified.auto_approvable is True


@pytest.mark.parametrize(
    "annotations",
    [
        {"destructiveHint": False},
        {"idempotentHint": True},
        {"destructiveHint": "false", "idempotentHint": "true"},
        {},
    ],
)
def test_gate_1_needs_every_claim(annotations) -> None:
    """Including the `is True`/`is False` discipline: stringly annotations are not claims."""
    classified = classify_tool(
        "sync_issue", server="acme", annotations=annotations, trust=ServerTrust.VETTED
    )
    assert classified.auto_approvable is False


# ---------------------------------------------------------------------------
# Reads and lifecycle
# ---------------------------------------------------------------------------


def test_a_read_is_unaffected_and_is_not_reported_as_pre_authorised() -> None:
    """A read never needed a prompt, so it must not be logged as an auto-approved
    ACTION — that would put noise in the log where accountability belongs."""
    name = _record(auto_approvable=True, pre_authorised=True, mode="read")

    assert needs_approval(name) is False
    assert external_tool_was_pre_authorised(name) is False


def test_forgetting_a_server_revokes_the_release() -> None:
    from navig.tools.approval import forget_external_tools

    name = _record(auto_approvable=True, pre_authorised=True)
    assert needs_approval(name) is False

    forget_external_tools("acme")
    assert needs_approval(name) is True, (
        "a disconnected server's pre-authorisation outlived it"
    )


def test_first_party_tools_are_untouched() -> None:
    """The two-gate release is deliberately external-only: a first-party tool has no
    server-side gate 1, so extending it would be a one-gate allowlist — a narrower YOLO."""
    assert needs_approval("bash_exec") is True
    assert external_tool_was_pre_authorised("bash_exec") is False


def test_re_recording_replaces_rather_than_accumulates() -> None:
    """A server that drops a claim on reconnect must lose the release."""
    name = _record(auto_approvable=True, pre_authorised=True)
    assert needs_approval(name) is False

    _record(auto_approvable=False, pre_authorised=True)
    assert needs_approval(name) is True


# ---------------------------------------------------------------------------
# Config → gate 2
# ---------------------------------------------------------------------------


@pytest.fixture
def trust_config(monkeypatch):
    section: dict = {}

    class _CM:
        @staticmethod
        def get(key, default=None):
            return section if key == "mcp.trust" else default

    monkeypatch.setattr("navig.config.get_config_manager", lambda *a, **k: _CM())
    return section


def test_nothing_is_pre_authorised_by_default(trust_config) -> None:
    from navig.mcp.trust import auto_approve_tools_for_server

    assert auto_approve_tools_for_server("acme") == ()


def test_the_operator_can_name_tools(trust_config) -> None:
    from navig.mcp.trust import auto_approve_tools_for_server

    trust_config["servers"] = {
        "acme": {"tier": "vetted", "auto_approve": ["sync_issue", "touch"]}
    }
    assert auto_approve_tools_for_server("acme") == ("sync_issue", "touch")


def test_a_comma_string_works(trust_config) -> None:
    """`navig config set` cannot write a YAML list."""
    from navig.mcp.trust import auto_approve_tools_for_server

    trust_config["servers"] = {"acme": {"auto_approve": "a, b"}}
    assert auto_approve_tools_for_server("acme") == ("a", "b")


@pytest.mark.parametrize("junk", [True, 5, {"a": 1}, None])
def test_an_unusable_value_pre_authorises_nothing(trust_config, junk) -> None:
    """Fail-closed in the grant direction: garbage must not become 'allow everything'."""
    from navig.mcp.trust import auto_approve_tools_for_server

    trust_config["servers"] = {"acme": {"auto_approve": junk}}
    assert auto_approve_tools_for_server("acme") == ()


def test_scope_and_auto_approve_are_independent(trust_config) -> None:
    """`tools` restricts what may be called; `auto_approve` releases the prompt. A tool
    can be in scope and still ask, which is the normal case."""
    from navig.mcp.trust import allowed_tools_for_server, auto_approve_tools_for_server

    trust_config["servers"] = {
        "acme": {"tier": "vetted", "tools": ["a", "b"], "auto_approve": ["a"]}
    }
    assert allowed_tools_for_server("acme") == ("a", "b")
    assert auto_approve_tools_for_server("acme") == ("a",)


# ---------------------------------------------------------------------------
# End to end through the real call path
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, tool_name: str, annotations: dict | None) -> None:
        from navig.mcp.client import MCPClientConfig
        from navig.mcp.protocol import MCPTool

        self.id = "acme"
        self.is_connected = True
        self.config = MCPClientConfig(id="acme", command="fake")
        self.called: list[str] = []
        self.tools = [
            MCPTool(
                name=tool_name,
                description="Syncs an issue.",
                input_schema={},
                server_id="acme",
                annotations=annotations or {},
            )
        ]

    async def call_tool(self, name: str, arguments: dict) -> str:
        self.called.append(name)
        return "DONE"


@pytest.fixture
def wired(monkeypatch):
    """A vetted server whose one tool the operator has pre-authorised."""
    monkeypatch.setattr("navig.mcp.trust.trust_for_server", lambda _s: ServerTrust.VETTED)
    monkeypatch.setattr(
        "navig.mcp.trust.auto_approve_tools_for_server", lambda _s: ("sync_issue",)
    )

    from navig.mcp.registry import MCPClientManager

    mgr = MCPClientManager()
    client = _FakeClient("sync_issue", VETTED_IDEMPOTENT)
    mgr._clients["acme"] = client  # noqa: SLF001
    return mgr, client


async def test_a_pre_authorised_call_runs_without_reaching_the_backend(wired) -> None:
    from navig.tools.approval import ApprovalDecision, get_approval_gate

    mgr, client = wired

    async def _explode(_req) -> ApprovalDecision:
        raise AssertionError("a doubly-gated action must not prompt")

    get_approval_gate().backend = _explode

    assert await mgr.call_tool("sync_issue", {"id": 1}) == "DONE"
    assert client.called == ["sync_issue"]


async def test_it_is_still_written_to_the_audit_log(wired, monkeypatch) -> None:
    """An unprompted write nobody can account for afterwards is the thing this whole
    mechanism exists to avoid."""
    import navig.tools.approval as approval

    records: list[dict] = []

    class _Log:
        @staticmethod
        def record(**kwargs):
            records.append(kwargs)

    monkeypatch.setattr(approval, "_bound_audit_log", _Log())

    mgr, _ = wired
    await mgr.call_tool("sync_issue", {"id": 1})

    assert len(records) == 1, "a pre-authorised action left no audit entry"
    entry = records[0]
    assert entry["action"] == "tool.execute.mcp__acme__sync_issue"
    assert entry["status"] == "approved"
    assert entry["metadata"]["auto_approved"] is True
    assert entry["metadata"]["enabled_by"] == "mcp.trust.servers.acme.auto_approve"


async def test_a_tool_the_operator_did_not_name_still_prompts(monkeypatch) -> None:
    monkeypatch.setattr("navig.mcp.trust.trust_for_server", lambda _s: ServerTrust.VETTED)
    monkeypatch.setattr(
        "navig.mcp.trust.auto_approve_tools_for_server", lambda _s: ("something_else",)
    )

    from navig.mcp.registry import MCPClientManager
    from navig.tools.approval import ApprovalDecision, get_approval_gate

    async def _deny(_req) -> ApprovalDecision:
        return ApprovalDecision.DENIED

    get_approval_gate().backend = _deny

    mgr = MCPClientManager()
    client = _FakeClient("sync_issue", VETTED_IDEMPOTENT)
    mgr._clients["acme"] = client  # noqa: SLF001

    with pytest.raises(PermissionError):
        await mgr.call_tool("sync_issue", {})
    assert client.called == []


async def test_a_read_is_not_audited_as_an_auto_approval(monkeypatch) -> None:
    """Reads run unprompted for a different reason; logging them here would bury the
    entries that carry accountability."""
    import navig.tools.approval as approval

    monkeypatch.setattr("navig.mcp.trust.trust_for_server", lambda _s: ServerTrust.VETTED)
    monkeypatch.setattr(
        "navig.mcp.trust.auto_approve_tools_for_server", lambda _s: ("sync_issue",)
    )
    records: list[dict] = []
    monkeypatch.setattr(
        approval, "_bound_audit_log", type("L", (), {"record": lambda **k: records.append(k)})()
    )

    from navig.mcp.registry import MCPClientManager

    mgr = MCPClientManager()
    client = _FakeClient("sync_issue", {"readOnlyHint": True})
    mgr._clients["acme"] = client  # noqa: SLF001

    await mgr.call_tool("sync_issue", {})

    assert client.called == ["sync_issue"]
    assert records == []


async def test_a_broken_audit_log_does_not_break_the_call(wired, monkeypatch) -> None:
    """A missing audit line is bad; a tool call that fails because logging it failed is
    worse."""
    import navig.tools.approval as approval

    class _Boom:
        @staticmethod
        def record(**kwargs):
            raise RuntimeError("log on fire")

    monkeypatch.setattr(approval, "_bound_audit_log", _Boom())

    mgr, client = wired
    assert await mgr.call_tool("sync_issue", {}) == "DONE"
    assert client.called == ["sync_issue"]
