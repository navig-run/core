"""A remediation mission runs unasked, cannot change anything unasked, and says
what it found.

* `_run_agent` runs under a MissionGrant — the agent loop's tool gate auto-
  approves read-only diagnostics and denies the rest with a reason. No prompt.
* `remediate` resolves to AUTO by default (`missions.remediate.autonomy`
  overrides): 80 of this operator's 84 remediate missions had ended "approval
  denied or timed out"; under the grant the mission-level prompt bought nothing.
* A finished system mission is pushed as `mission_complete` — a registered
  type that nothing dispatched, which is how three missions rewrote the
  operator's LLM routing and told nobody.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from navig.contracts.mission import Mission
from navig.contracts.store import RuntimeStore
from navig.missions.executor import Autonomy, MissionExecutor
from navig.tools import approval as ap


def _gateway(missions_cfg: dict | None = None):
    return SimpleNamespace(
        config_manager=SimpleNamespace(global_config={"missions": missions_cfg or {}})
    )


def _executor(tmp_path, missions_cfg: dict | None = None) -> MissionExecutor:
    return MissionExecutor(gateway=_gateway(missions_cfg), store=RuntimeStore(store_dir=tmp_path))


# ── the grant wraps the agent run ────────────────────────────────────────────


async def test_the_agent_runs_under_a_grant_naming_the_mission(tmp_path, monkeypatch):
    ex = _executor(tmp_path, {"allowed_commands": ["navig mode set *"]})
    mission = Mission(
        title="Remediate health issues", capability="remediate", payload={"issues": ["x"]}
    )
    seen: dict = {}

    class _Agent:
        async def run_agentic(self, **kw):
            seen["grant"] = ap.current_mission_grant()
            return "diagnosed"

    monkeypatch.setattr("navig.agent.conv.ConversationalAgent", _Agent)

    assert await ex._run_agent(mission) == "diagnosed"
    g = seen["grant"]
    assert g is not None and g.mission_id == mission.mission_id
    assert g.title == "Remediate health issues"
    assert g.allowed_commands == ("navig mode set *",)
    assert g.enabled_by == "missions.remediate.autonomy"
    assert ap.current_mission_grant() is None, "the grant must not leak past the run"


async def test_inside_the_run_a_mutating_tool_is_denied_not_prompted(tmp_path, monkeypatch):
    """End to end through the real gate: the agent asks for a write, gets a denial
    string back, and the prompting backend is never reached."""
    monkeypatch.delenv("NAVIG_ALLOW_ALL_COMMANDS", raising=False)
    ap.reset_approval_gate()
    paged: list = []

    async def page(req):
        paged.append(req.tool_name)
        return ap.ApprovalDecision.APPROVED

    ap.get_approval_gate().backend = page
    try:
        ex = _executor(tmp_path)
        mission = Mission(title="Remediate health issues", capability="remediate")
        results: dict = {}

        class _Agent:
            async def run_agentic(self, **kw):
                results["read"] = await ap.gate_agent_tool_call(
                    "bash_exec", parameters={"command": "navig service status"}
                )
                results["write"] = await ap.gate_agent_tool_call(
                    "bash_exec", parameters={"command": "navig service restart"}
                )
                return "done"

        monkeypatch.setattr("navig.agent.conv.ConversationalAgent", _Agent)
        await ex._run_agent(mission)
    finally:
        ap.reset_approval_gate()

    assert results["read"] is None
    assert results["write"] and "Denied inside mission" in results["write"]
    assert paged == [], "seven prompts in one minute — never again"


# ── autonomy ─────────────────────────────────────────────────────────────────


def test_remediate_is_auto_by_default(tmp_path):
    ex = _executor(tmp_path)
    assert ex._resolve_autonomy(Mission(title="r", capability="remediate")) == Autonomy.AUTO


def test_the_operator_can_put_remediation_back_behind_a_prompt(tmp_path):
    ex = _executor(tmp_path, {"remediate": {"autonomy": "approval"}})
    assert ex._resolve_autonomy(Mission(title="r", capability="remediate")) == Autonomy.APPROVAL


def test_other_capabilities_keep_the_global_level(tmp_path, monkeypatch):
    ex = _executor(tmp_path)
    monkeypatch.setattr(ex, "_global_autonomy", lambda: Autonomy.APPROVAL)
    assert ex._resolve_autonomy(Mission(title="p", capability="proactive")) == Autonomy.APPROVAL
    assert ex._resolve_autonomy(Mission(title="b", capability="board_card")) == Autonomy.APPROVAL


def test_mission_metadata_still_wins(tmp_path):
    ex = _executor(tmp_path)
    m = Mission(title="r", capability="remediate", metadata={"autonomy": "draft"})
    assert ex._resolve_autonomy(m) == Autonomy.DRAFT


# ── the outcome is pushed ────────────────────────────────────────────────────


async def test_a_finished_remediation_is_pushed_as_mission_complete(tmp_path, monkeypatch):
    ex = _executor(tmp_path)
    mission = Mission(
        title="Remediate health issues", capability="remediate", metadata={"autonomy": "draft"}
    )
    ex.store.create_mission(mission)
    sent: list = []

    async def fake_dispatch(type_key, title, body, **kw):
        sent.append((type_key, title, body, kw))

    monkeypatch.setattr("navig.notify.router.dispatch", fake_dispatch)

    async def _draft(m):
        return "Root cause: disk / at 91%. Recommended: prune /var/log (needs you)."

    monkeypatch.setattr(ex, "_run_draft", _draft)

    await ex._execute(mission)

    assert len(sent) == 1
    type_key, title, body, kw = sent[0]
    assert type_key == "mission_complete"
    assert "Remediate health issues" in title and "succeeded" in title
    assert "disk / at 91%" in body
    assert kw["data"]["mission_id"] == mission.mission_id


async def test_a_board_card_does_not_push(tmp_path, monkeypatch):
    ex = _executor(tmp_path)
    sent: list = []

    async def fake_dispatch(*a, **kw):
        sent.append(a)

    monkeypatch.setattr("navig.notify.router.dispatch", fake_dispatch)

    await ex._notify_complete(Mission(title="card", capability="board_card"))

    assert sent == []


async def test_a_failed_push_never_fails_the_mission(tmp_path, monkeypatch):
    ex = _executor(tmp_path)
    mission = Mission(title="r", capability="remediate", metadata={"autonomy": "draft"})
    ex.store.create_mission(mission)

    async def boom(*a, **kw):
        raise RuntimeError("router down")

    monkeypatch.setattr("navig.notify.router.dispatch", boom)

    async def _draft(m):
        return "ok"

    monkeypatch.setattr(ex, "_run_draft", _draft)

    result = await ex._execute(mission)

    assert result.status.value == "succeeded"


def test_mission_complete_is_a_registered_notification_type():
    from navig.notify.types import NOTIFICATION_TYPES

    assert any(t["key"] == "mission_complete" for t in NOTIFICATION_TYPES)


@pytest.mark.parametrize(
    "raw, expect", [(["a *", " ", "b"], ["a *", "b"]), ("one", ["one"]), (None, [])]
)
def test_allowed_commands_config_shapes(tmp_path, raw, expect):
    ex = _executor(tmp_path, {"allowed_commands": raw})
    assert ex._allowed_commands() == expect


# ── the prompt states the rules ──────────────────────────────────────────────


def test_the_remediate_prompt_tells_the_agent_it_is_read_only_and_must_report(tmp_path):
    ex = _executor(tmp_path)
    m = Mission(title="r", capability="remediate", payload={"issues": ["[HIGH] disk / at 91%"]})

    prompt = ex._build_prompt(m)

    assert "READ-ONLY" in prompt and "will be refused" in prompt
    assert "do NOT retry" in prompt
    assert "report" in prompt.lower() and "[HIGH] disk / at 91%" in prompt
    assert "pre-authorised" not in prompt, "no allowed_commands → no dangling clause"


def test_the_remediate_prompt_names_the_operators_allowed_commands(tmp_path):
    ex = _executor(tmp_path, {"allowed_commands": ["navig mode set *"]})
    m = Mission(title="r", capability="remediate", payload={"issues": ["x"]})

    assert "`navig mode set *`" in ex._build_prompt(m)


# ── a run that produced nothing is not a success ─────────────────────────────


async def test_a_turn_capped_run_is_a_failed_mission_not_a_success(tmp_path, monkeypatch):
    """The operator's feed read "Remediate health issues — succeeded: Agent reached
    the 8-turn limit without a final answer". A run that produced nothing failed."""
    from navig.contracts.mission import MissionStatus

    ex = _executor(tmp_path)
    mission = Mission(
        title="Remediate health issues", capability="remediate", metadata={"autonomy": "auto"}
    )
    ex.store.create_mission(mission)

    class _Agent:
        last_run_incomplete = None

        async def run_agentic(self, **kw):
            self.last_run_incomplete = "turn_limit"
            return "Agent reached the 8-turn limit without a final answer. Try a more specific request."

    monkeypatch.setattr("navig.agent.conv.ConversationalAgent", _Agent)
    monkeypatch.setattr(ex, "_verify_mission", _no_verdict)
    sent: list = []

    async def fake_dispatch(type_key, title, body, **kw):
        sent.append((title, body))

    monkeypatch.setattr("navig.notify.router.dispatch", fake_dispatch)

    result = await ex._execute(mission)

    assert result.status == MissionStatus.FAILED
    assert "turn_limit" in (result.error or "")
    assert sent and "failed" in sent[0][0] and "8-turn limit" in sent[0][1]


async def test_a_real_answer_is_a_success(tmp_path, monkeypatch):
    from navig.contracts.mission import MissionStatus

    ex = _executor(tmp_path)
    mission = Mission(title="r", capability="remediate", metadata={"autonomy": "auto"})
    ex.store.create_mission(mission)

    class _Agent:
        last_run_incomplete = None

        async def run_agentic(self, **kw):
            return "Root cause: disk full. Fix: prune /var/log."

    monkeypatch.setattr("navig.agent.conv.ConversationalAgent", _Agent)
    monkeypatch.setattr(ex, "_verify_mission", _no_verdict)

    result = await ex._execute(mission)

    assert result.status == MissionStatus.SUCCEEDED


async def _no_verdict(mission):
    return None


def test_the_agent_sets_the_structured_signal_on_both_incomplete_paths():
    """No caller should have to pattern-match the prose."""
    import ast
    import pathlib

    from navig.agent.conv import agent as agent_mod

    src = pathlib.Path(agent_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    values = {
        n.value.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Attribute) and t.attr == "last_run_incomplete" for t in n.targets)
        and isinstance(n.value, ast.Constant)
    }
    assert {"turn_limit", "empty_response"} <= values, values
