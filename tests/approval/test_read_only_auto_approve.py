"""Chat-path read-only shell commands run unprompted (approval.auto_approve_read_only).

2026-10-13: three `grep`/`ls` cards looped in Telegram — "Approval Required"
for purely diagnostic commands, a denial the model then retried (a fresh card
each time), and a re-tap reported as "answered after it had already expired".
The mission grant already trusted `shell_readonly`; this extends it to the
chat path under `approval.auto_approve_read_only` (default on), audited every
time naming the key. Anything that writes still pages, and `false` restores
the old prompts wholesale.
"""

from __future__ import annotations

import pytest

from navig.tools import approval as ap

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _prompting_backend(monkeypatch):
    """A gate whose backend PAGES — the test asserts when it is (not) reached."""
    monkeypatch.delenv("NAVIG_ALLOW_ALL_COMMANDS", raising=False)
    ap.reset_approval_gate()
    paged: list[str] = []
    decision = {"value": ap.ApprovalDecision.APPROVED}

    async def page(req: ap.ApprovalRequest) -> ap.ApprovalDecision:
        paged.append(req.tool_name)
        return decision["value"]

    ap.get_approval_gate().backend = page
    yield paged, decision
    ap.reset_approval_gate()


@pytest.fixture
def audited(monkeypatch):
    rows: list[dict] = []

    class _Log:
        def record(self, **kw):
            rows.append(kw)

    monkeypatch.setattr(ap, "_bound_audit_log", _Log())
    return rows


async def test_a_read_only_chat_command_runs_unprompted_and_is_audited(
    _prompting_backend, audited
):
    paged, _ = _prompting_backend
    denial = await ap.gate_agent_tool_call(
        "bash_exec", parameters={"command": "grep -n error x.log | head -20"}
    )

    assert denial is None
    assert paged == [], "a proven read must not build an approval card"
    assert audited and audited[0]["metadata"]["auto_approved"] is True
    assert "approval.auto_approve_read_only" in audited[0]["metadata"]["enabled_by"]


async def test_the_incident_commands_are_read_only(_prompting_backend, audited):
    """The exact three commands that looped cards on 2026-10-13."""
    paged, _ = _prompting_backend
    for cmd in (
        "grep -ril --include='*' -e subscription -e renewal /root/.hermes2 2>/dev/null | head -20",
        "ls .navig/ 2>/dev/null; ls .navig/sessions 2>/dev/null | tail -5",
        'grep -in "subscription" .navig/navig.log | tail -30',
    ):
        assert await ap.gate_agent_tool_call(
            "bash_exec", parameters={"command": cmd}
        ) is None, cmd
    assert paged == []


async def test_a_mutating_command_still_pages(_prompting_backend):
    paged, _ = _prompting_backend
    denial = await ap.gate_agent_tool_call(
        "bash_exec", parameters={"command": "rm -rf /tmp/x"}
    )

    assert denial is None  # fixture approves, but only AFTER asking
    assert paged == ["bash_exec"], "writes must still page the operator"


async def test_a_denied_mutation_tells_the_model_not_to_retry(_prompting_backend):
    _, decision = _prompting_backend
    decision["value"] = ap.ApprovalDecision.DENIED
    denial = await ap.gate_agent_tool_call(
        "bash_exec", parameters={"command": "rm -rf /tmp/x"}
    )

    assert denial and denial.startswith("[Denied")
    assert "Do not retry" in denial, "the old bare denial looped approval cards"
    assert "bash_exec" in denial


async def test_disabling_the_config_key_restores_prompts(_prompting_backend, monkeypatch):
    import navig.config as cfg

    real_get = cfg.get

    def fake_get(key, default=None, *a, **kw):
        if key == "approval.auto_approve_read_only":
            return False
        return real_get(key, default, *a, **kw)

    monkeypatch.setattr(cfg, "get", fake_get)
    paged, _ = _prompting_backend
    denial = await ap.gate_agent_tool_call(
        "bash_exec", parameters={"command": "grep -n error x.log"}
    )

    assert denial is None and paged == ["bash_exec"], "the key must disable the bypass"


def test_the_config_reader_survives_a_string_false(monkeypatch):
    """`navig config set approval.auto_approve_read_only false` stores "false" —
    a raw truthiness check would keep the bypass ON after the operator turned
    it off."""
    import navig.config as cfg

    monkeypatch.setattr(cfg, "get", lambda k, d=None: "false" if "auto_approve" in k else d)
    assert ap._auto_approve_read_only() is False

    monkeypatch.setattr(cfg, "get", lambda k, d=None: "true" if "auto_approve" in k else d)
    assert ap._auto_approve_read_only() is True
