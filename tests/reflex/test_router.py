"""Plan/commit semantics: purity, exactly-once, replay, profiles, no commit fallback.

Everything here runs through injected fakes: the resolver is a static list and the
command runner records argv, so "mutates nothing" and "ran exactly once" are proven
by what the fake saw — never by a network or a sleep.
"""

from __future__ import annotations

import pytest

from navig.reflex.answer_bridge import answer_as_dict
from navig.reflex.errors import PlanExpired, PlanUnknown
from navig.reflex.plans import PLAN_TTL_SECONDS
from navig.reflex.service import ReflexRouter

CONTRACT_KEYS = {"speech", "display_text", "ok", "data", "plan"}


def _payload(answer) -> dict:
    return answer_as_dict(answer)


# ── plan purity ─────────────────────────────────────────────────────────────


def test_plan_mints_a_token_and_mutates_nothing(router: ReflexRouter, runner) -> None:
    result = router.plan("turn off the desk lamp")
    assert result.matched is True
    assert result.committed is False
    assert result.plan is not None
    assert result.plan.token.startswith("plan-")
    assert runner.calls == [], f"plan ran a command: {runner.calls}"
    payload = _payload(result.answer)
    assert CONTRACT_KEYS <= set(payload)
    plan = payload["plan"]
    assert plan["tool"] == "home.off"
    assert plan["slots"]["device"] == "desk lamp"  # the documented plan metadata
    assert plan["targets"][0]["id"] == "govee:desk-lamp"
    assert plan["blast_radius"]["kind"] == "single"
    assert plan["profile"] == "fast"
    assert plan["next"].endswith(plan["commit_token"])
    assert payload["data"]["executed"] is False


def test_plan_answer_names_the_tool_target_and_token(router: ReflexRouter) -> None:
    payload = _payload(router.plan("turn off the desk lamp").answer)
    assert "home.off" in payload["speech"]
    assert payload["suggestions"] == [f"navig ask --commit {payload['plan']['commit_token']}"]


# ── commit: exactly once, replay returns the stored Answer ─────────────────


def test_commit_runs_once_and_replay_is_a_no_op(router: ReflexRouter, runner) -> None:
    plan = router.plan("turn off the desk lamp").plan
    assert plan is not None
    first = _payload(router.commit(plan.token))
    assert first["ok"] is True
    assert first["data"]["executed"] is True
    assert len(runner.calls) == 1
    # DEFECT-class regression: the home CLI takes the INVENTORY id, not `provider:id`.
    assert "home off desk-lamp" in " ".join(runner.calls[0])
    assert "govee:desk-lamp" not in " ".join(runner.calls[0])

    replay = _payload(router.commit(plan.token))
    assert len(runner.calls) == 1, "replay fired the command again"
    assert replay == first, "replay did not return the stored Answer verbatim"


def test_the_plan_keeps_the_stable_endpoint_id_while_the_command_uses_the_home_id(
    router: ReflexRouter,
) -> None:
    payload = _payload(router.plan("turn off the desk lamp").answer)
    assert payload["plan"]["targets"][0]["id"] == "govee:desk-lamp"
    assert payload["plan"]["blast_radius"]["targets"][0]["id"] == "govee:desk-lamp"
    assert payload["plan"]["command"] == ["navig", "home", "off", "desk-lamp"]


def test_commit_of_an_unknown_token_raises_with_a_hint(router: ReflexRouter) -> None:
    with pytest.raises(PlanUnknown) as info:
        router.commit("plan-nope")
    assert "--plan" in info.value.hint


def test_commit_of_an_expired_token_raises(router: ReflexRouter, clock, runner) -> None:
    plan = router.plan("turn off the desk lamp").plan
    assert plan is not None
    clock.advance(PLAN_TTL_SECONDS + 5)
    with pytest.raises(PlanExpired) as info:
        router.commit(plan.token)
    assert "run --plan again" in info.value.hint
    assert runner.calls == []


def test_an_interrupted_commit_does_not_rerun_the_action(
    make_router, plan_store, runner
) -> None:
    """A process killed mid-commit leaves ``committed`` with no result: never re-fire."""
    router = make_router()
    plan = router.plan("turn off the desk lamp").plan
    assert plan is not None
    plan_store.claim(plan.token)  # claimed, never finished — the crash window
    payload = _payload(router.commit(plan.token))
    assert runner.calls == []
    assert payload["ok"] is False
    assert payload["data"]["interrupted"] is True


def test_a_failing_command_is_recorded_and_replayed_without_rerun(
    make_router, plan_store, make_runner
) -> None:
    runner = make_runner(fail=("home off",))
    router = make_router(runner=runner)
    plan = router.plan("turn off the desk lamp").plan
    assert plan is not None
    failure = _payload(router.commit(plan.token))
    assert failure["ok"] is False
    assert "boom" in failure["speech"]
    assert len(runner.calls) == 1
    replay = _payload(router.commit(plan.token))
    assert len(runner.calls) == 1
    assert replay == failure
    stored = plan_store.get(plan.token)
    assert stored is not None and stored.state == "failed"


# ── profiles ───────────────────────────────────────────────────────────────


def test_fast_auto_commits_one_reversible_action(router: ReflexRouter, runner) -> None:
    result = router.route("turn on the desk lamp", profile="fast")
    assert result.committed is True
    assert len(runner.calls) == 1


def test_fast_plans_a_batch_and_never_executes_it(router: ReflexRouter, runner) -> None:
    result = router.route("turn off everything", profile="fast")
    assert result.matched is True
    assert result.committed is False
    assert result.plan is not None
    assert result.plan.destructive is True
    assert result.plan.blast_radius["kind"] == "batch"
    assert runner.calls == []


@pytest.mark.parametrize("profile", ["safe", "campaign"])
def test_safe_and_campaign_never_auto_execute(router: ReflexRouter, runner, profile) -> None:
    result = router.route("turn on the desk lamp", profile=profile)
    assert result.matched is True
    assert result.committed is False
    assert result.plan is not None
    assert runner.calls == []


@pytest.mark.parametrize("profile", ["safe", "fast", "campaign"])
def test_read_only_runs_in_every_profile(router: ReflexRouter, profile: str) -> None:
    result = router.route("home status", profile=profile)
    assert result.committed is True
    payload = _payload(result.answer)
    assert payload["ok"] is True
    assert payload["table"]["columns"] == ["Device", "Reach", "Power"]


# ── batch under one token: N idempotent commands, no tool fallback ─────────


def test_batch_commit_runs_one_command_per_target_under_one_token(
    router: ReflexRouter, runner
) -> None:
    plan = router.plan("turn off everything").plan
    assert plan is not None
    # Every power-capable endpoint (the sensor is not one) — four, in inventory order.
    assert plan.blast_radius["count"] == 4
    answer = _payload(router.commit(plan.token))
    assert answer["ok"] is True
    assert len(runner.calls) == 4
    assert all("home off" in " ".join(call) for call in runner.calls)
    # One tool, one token — a replay of the same token runs nothing further.
    router.commit(plan.token)
    assert len(runner.calls) == 4


# ── slot requests and classification-time fallback ─────────────────────────


def test_ambiguous_target_returns_a_slot_request_and_no_token(make_router) -> None:
    from navig.reflex.resolve import StaticResolver

    router = make_router(
        resolver=StaticResolver(
            [
                {
                    "id": "govee:desk-lamp",
                    "name": "desk lamp",
                    "room": "office",
                    "type": "devices.types.light",
                    "capabilities": [{"type": "devices.capabilities.on_off"}],
                },
                {
                    "id": "govee:floor-lamp",
                    "name": "floor lamp",
                    "room": "office",
                    "type": "devices.types.light",
                    "capabilities": [{"type": "devices.capabilities.on_off"}],
                },
            ]
        )
    )
    result = router.route("turn off the lamp")
    assert result.needs_slot == "device"
    assert result.plan is None
    assert result.committed is False
    payload = _payload(result.answer)
    assert payload["data"]["needs_slot"] == "device"
    assert len(payload["data"]["candidates"]) == 2
    # The operator is asked with the device names, not just told "no target".
    assert set(payload["suggestions"]) == {"desk lamp", "floor lamp"}


def test_a_near_miss_target_asks_with_the_inventory_names(router: ReflexRouter, runner) -> None:
    result = router.route("turn off the aquarium")
    assert result.needs_slot == "device"
    assert result.plan is None
    assert runner.calls == []
    payload = _payload(result.answer)
    assert "Desk lamp" in payload["suggestions"]


def test_unmatched_utterance_is_not_a_dead_end(router: ReflexRouter, runner) -> None:
    result = router.route("restart nginx on prod")
    assert result.matched is False
    assert result.answer is None
    assert runner.calls == []


# ── answer envelope ────────────────────────────────────────────────────────


def test_committed_answer_carries_the_contract_fields_and_plan(router: ReflexRouter) -> None:
    result = router.route("turn off the desk lamp")
    payload = _payload(result.answer)
    assert CONTRACT_KEYS <= set(payload)
    assert payload["intent"] == "home.off"
    assert payload["plan"]["tool"] == "home.off"
    assert payload["data"]["commands"][0]["argv"]
    assert payload["speech"]
    assert payload["display_text"]


def test_status_table_is_projected_from_the_command_payload(router: ReflexRouter) -> None:
    payload = _payload(router.route("home status").answer)
    assert payload["table"]["rows"] == [["Desk lamp", "online", "on"]]
    assert payload["json"]["count"] == 1


def test_coordination_inbox_answers_from_the_mailbox(router: ReflexRouter, runner) -> None:
    """The collective-coordination mailbox is a read-only Reflex surface (wired)."""
    result = router.route("check my inbox")
    assert result.matched is True
    assert result.committed is True  # read-only runs in every profile
    payload = _payload(result.answer)
    assert payload["ok"] is True
    assert payload["speech"] == "1 coordination message(s) waiting for operator."
    assert payload["table"]["columns"] == ["From", "Kind", "State", "Content"]
    assert payload["table"]["rows"] == [["agent:lead", "handoff", "persisted", "{'note': 'hi'}"]]
    # The command binds to the real CLI path (the executor shells it, never reimplements).
    assert "coordination inbox operator" in " ".join(runner.calls[0])
