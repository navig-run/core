"""Inside a mission, no tool call pages the operator.

2026-09-19 20:39–20:40: one "Remediate health issues" mission asked for approval
seven times — the mission, then six `tool bash_exec` prompts that did not even
say what they wanted to run. A mission is the unit the operator decides on; its
tool calls are not. Under a MissionGrant a read-only shell command runs and is
audited; anything else is denied with a reason the agent reads, so it reports
the action instead of asking a human who answered 3 of 84 such requests.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.tools import approval as ap

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _prompting_backend(monkeypatch):
    """A gate whose backend would PAGE — the test asserts it is never reached."""
    monkeypatch.delenv("NAVIG_ALLOW_ALL_COMMANDS", raising=False)
    ap.reset_approval_gate()
    paged: list[str] = []

    async def page(req: ap.ApprovalRequest) -> ap.ApprovalDecision:
        paged.append(req.tool_name)
        return ap.ApprovalDecision.APPROVED

    ap.get_approval_gate().backend = page
    yield paged
    ap.reset_approval_gate()


@pytest.fixture
def audited(monkeypatch):
    rows: list[dict] = []

    class _Log:
        def record(self, **kw):
            rows.append(kw)

    monkeypatch.setattr(ap, "_bound_audit_log", _Log())
    return rows


GRANT = ap.MissionGrant(mission_id="cbad7281abcd", title="Remediate health issues")


async def test_a_read_only_shell_command_runs_unprompted_and_is_audited(
    _prompting_backend, audited
):
    with ap.mission_grant(GRANT):
        denial = await ap.gate_agent_tool_call("bash_exec", parameters={"command": "navig doctor"})

    assert denial is None
    assert _prompting_backend == [], "the operator must not be paged inside a mission"
    assert audited and audited[0]["metadata"]["auto_approved"] is True
    assert "cbad7281" in audited[0]["metadata"]["enabled_by"]


async def test_a_mutating_shell_command_is_denied_with_a_reason_not_paged(_prompting_backend):
    with ap.mission_grant(GRANT):
        denial = await ap.gate_agent_tool_call(
            "bash_exec", parameters={"command": "navig mode set big_tasks --provider local"}
        )

    assert denial and "Denied inside mission" in denial
    assert "Remediate health issues" in denial
    assert "report this command" in denial, "the agent must be told to REPORT, not retry"
    assert _prompting_backend == []


async def test_an_operator_listed_pattern_is_allowed_and_audited(_prompting_backend, audited):
    grant = ap.MissionGrant(mission_id="m1", allowed_commands=("navig mode set *",))
    with ap.mission_grant(grant):
        denial = await ap.gate_agent_tool_call(
            "bash_exec", parameters={"command": "navig mode set research --model x"}
        )

    assert denial is None and _prompting_backend == []
    assert "allowed_commands" in audited[0]["metadata"]["enabled_by"]


async def test_a_non_shell_destructive_tool_is_denied_inside_a_mission(_prompting_backend):
    with ap.mission_grant(GRANT):
        denial = await ap.gate_agent_tool_call(
            "write_file", parameters={"path": "x", "content": "y"}
        )

    assert denial and "write_file" in denial and _prompting_backend == []


async def test_without_a_grant_the_backend_is_consulted_as_before(_prompting_backend):
    """Interactive chat keeps its prompts — the grant is scoped, not global."""
    denial = await ap.gate_agent_tool_call("bash_exec", parameters={"command": "navig doctor"})

    assert denial is None and _prompting_backend == ["bash_exec"]


async def test_the_grant_follows_the_mission_task_and_not_its_siblings(_prompting_backend):
    """A chat turn running concurrently with a mission must still be prompted."""
    seen: dict[str, object] = {}

    async def mission():
        with ap.mission_grant(GRANT):
            await asyncio.sleep(0.01)
            seen["mission"] = await ap.gate_agent_tool_call(
                "bash_exec", parameters={"command": "git status"}
            )

    async def chat():
        await asyncio.sleep(0.005)  # runs while the mission's grant is set
        seen["chat"] = await ap.gate_agent_tool_call(
            "bash_exec", parameters={"command": "git status"}
        )

    await asyncio.gather(mission(), chat())

    assert seen["mission"] is None and seen["chat"] is None
    assert _prompting_backend == ["bash_exec"], "exactly the chat call was paged"


async def test_a_safe_tool_needs_no_grant_and_no_prompt(_prompting_backend):
    with ap.mission_grant(GRANT):
        assert await ap.gate_agent_tool_call("read_file", parameters={"path": "x"}) is None
    assert _prompting_backend == []


def test_the_grant_context_resets_after_the_block():
    assert ap.current_mission_grant() is None
    with ap.mission_grant(GRANT):
        assert ap.current_mission_grant() is GRANT
    assert ap.current_mission_grant() is None


# ── the grant holds at the GATE, for every caller ────────────────────────────


async def test_the_gate_itself_honours_the_grant_for_a_direct_check(_prompting_backend, audited):
    """An MCP tool the mission's agent invokes goes `get_approval_gate().check(...)`
    directly (navig/mcp/registry.py) — not through gate_agent_tool_call. Inside a
    mission that must not page either."""
    with ap.mission_grant(GRANT):
        d = await ap.get_approval_gate().check(
            "mcp_somehost_write", "dangerous", {"x": 1}, reason="mcp", context={"mcp_server": "s"}
        )

    assert d == ap.ApprovalDecision.DENIED
    assert _prompting_backend == [], "a direct gate check inside a mission paged the operator"


async def test_the_gate_auto_approves_a_read_only_shell_call_directly(_prompting_backend, audited):
    with ap.mission_grant(GRANT):
        d = await ap.get_approval_gate().check(
            "bash_exec", "dangerous", {"command": "git status"}, reason="llm-parsed tool call"
        )

    assert d == ap.ApprovalDecision.APPROVED and _prompting_backend == []
    assert audited and "cbad7281" in audited[0]["metadata"]["enabled_by"]


def test_check_sync_carries_the_grant_into_its_worker_thread(_prompting_backend):
    """`check_sync` from inside a running loop hops to a worker thread, which starts
    with an EMPTY context — without the copy the grant vanished exactly there."""
    import asyncio

    seen: dict = {}

    async def inside_loop():
        with ap.mission_grant(GRANT):
            seen["d"] = ap.check_sync("bash_exec", "dangerous", {"command": "navig doctor"})

    asyncio.run(inside_loop())

    assert seen["d"] == ap.ApprovalDecision.APPROVED
    assert _prompting_backend == []


def test_check_sync_without_a_loop_also_sees_the_grant(_prompting_backend):
    with ap.mission_grant(GRANT):
        d = ap.check_sync("bash_exec", "dangerous", {"command": "navig service restart"})

    assert d == ap.ApprovalDecision.DENIED and _prompting_backend == []
