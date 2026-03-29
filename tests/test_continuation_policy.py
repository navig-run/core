from navig.core.continuation import (
    apply_busy_suppression,
    busy_window_seconds,
    classify_continuation_state,
    ContinuationPolicy,
    decision_sensitivity_for_profile,
    get_busy_suppression,
    merge_policy,
    normalize_profile_name,
    policy_from_context,
    should_auto_continue,
)


def test_policy_roundtrip_and_defaults():
    policy = policy_from_context({})
    assert isinstance(policy, ContinuationPolicy)
    assert policy.profile == "conservative"
    assert policy.enabled is False
    assert policy.cooldown_seconds == 20

    context = merge_policy({}, enabled=True, max_turns=3)
    updated = policy_from_context(context)
    assert updated.enabled is True
    assert updated.max_turns == 3


def test_should_auto_continue_enforces_limits_and_decision_point():
    policy = ContinuationPolicy(enabled=True, paused=False, skip_next=False, cooldown_seconds=0, max_turns=2, turns_used=0)
    ok, reason = should_auto_continue("Should I continue with deployment?", policy)
    assert ok is True
    assert reason == "ok"

    capped = ContinuationPolicy(enabled=True, paused=False, skip_next=False, cooldown_seconds=0, max_turns=1, turns_used=1)
    ok2, reason2 = should_auto_continue("Should I continue?", capped)
    assert ok2 is False
    assert reason2 == "max_turns"

    no_decision = ContinuationPolicy(enabled=True, paused=False, skip_next=False, cooldown_seconds=0, max_turns=2, turns_used=0)
    ok3, reason3 = should_auto_continue("Deployment finished successfully.", no_decision)
    assert ok3 is False
    assert reason3 == "no_decision_point"


def test_profile_defaults_and_normalization():
    normalized = normalize_profile_name("BALANCED")
    assert normalized == "balanced"

    context = merge_policy({}, profile="balanced")
    policy = policy_from_context(context)
    assert policy.profile == "balanced"
    assert policy.cooldown_seconds == 10
    assert policy.max_turns == 3


def test_classifier_states():
    state1, reason1 = classify_continuation_state("Should I continue with deployment?")
    assert state1 == "continue"
    assert reason1 == "continue_signal"

    state2, reason2 = classify_continuation_state("Choose option A or B for rollout strategy")
    assert state2 == "choice"
    assert reason2 == "choice_signal"

    state3, reason3 = classify_continuation_state("Still working on this, one moment")
    assert state3 == "wait"
    assert reason3 == "wait_signal"


def test_busy_suppression_blocks_auto_continue_until_window_expires():
    policy = ContinuationPolicy(
        enabled=True,
        paused=False,
        skip_next=False,
        cooldown_seconds=0,
        max_turns=2,
        turns_used=0,
    )
    context = apply_busy_suppression({}, "wait", "wait_signal")
    active, reason, busy_until = get_busy_suppression(context)
    assert active is True
    assert reason == "wait_signal"
    assert busy_until

    ok, result_reason = should_auto_continue("Should I continue now?", policy, context)
    assert ok is False
    assert result_reason == "busy_suppressed:wait_signal"


def test_profile_specific_busy_windows_are_applied():
    assert busy_window_seconds("conservative", "wait") == 45
    assert busy_window_seconds("balanced", "wait") == 30
    assert busy_window_seconds("aggressive", "wait") == 15

    context = apply_busy_suppression({}, "blocked", "blocked_signal", profile="aggressive")
    continuation = context.get("continuation") or {}
    assert continuation.get("busy_window_seconds") == 60


def test_profile_decision_sensitivity_levels():
    assert decision_sensitivity_for_profile("conservative") == "strict"
    assert decision_sensitivity_for_profile("balanced") == "standard"
    assert decision_sensitivity_for_profile("aggressive") == "eager"


def test_aggressive_profile_accepts_soft_continue_prompt():
    soft_prompt = "Proceed with the next step?"
    conservative = ContinuationPolicy(
        profile="conservative",
        enabled=True,
        paused=False,
        skip_next=False,
        cooldown_seconds=0,
        max_turns=2,
        turns_used=0,
    )
    aggressive = ContinuationPolicy(
        profile="aggressive",
        enabled=True,
        paused=False,
        skip_next=False,
        cooldown_seconds=0,
        max_turns=5,
        turns_used=0,
    )

    ok_conservative, _ = should_auto_continue(soft_prompt, conservative)
    ok_aggressive, reason_aggressive = should_auto_continue(soft_prompt, aggressive)

    assert ok_conservative is False
    assert ok_aggressive is True
    assert reason_aggressive == "ok"
