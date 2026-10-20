"""The Telegram message brief and the shadow judges (decision model, `navig decider`).

Both are SHADOW-ONLY: they record whether Clef agreed with the logic that decides today
and must never change what that logic does. These tests pin that contract, the
order-independence of a brief (the existing classifier may answer before or after Clef),
and that every hook is still wired where the comparison has to happen.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from navig.agent import decide, judges, message_brief


def _outcome(answers: dict[str, str], use: str = message_brief.USE) -> decide.Outcome:
    return decide.Outcome(
        use=use,
        answers={k: decide.Decision(v, 0.9, {v: 0.9}) for k, v in answers.items()},
        live=False,
        min_confidence=0.5,
    )


@pytest.fixture
def brief(monkeypatch):
    """Brief active; Clef's answer released by the test (to control ordering)."""
    message_brief.reset_for_tests()
    gate = threading.Event()
    state = {"compared": [], "answers": {"mode": "REASON", "reminder": "no", "depth": "deep"}, "calls": 0}

    def fake_outcome(text, questions, *, use, images=None):
        state["calls"] += 1
        state["qids"] = [q.id for q in questions]
        gate.wait(5)
        return _outcome(state["answers"])

    monkeypatch.setattr(decide, "is_active", lambda use: use == message_brief.USE)
    monkeypatch.setattr(decide, "outcome", fake_outcome)
    monkeypatch.setattr(
        decide, "compare",
        lambda out, qid, legacy, sample="": state["compared"].append((qid, out.get(qid).answer, legacy)),
    )
    yield state, gate
    gate.set()
    message_brief.reset_for_tests()


def _wait_for(pred, timeout=5.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


# ── the brief ────────────────────────────────────────────────


def test_inactive_brief_does_nothing(monkeypatch):
    message_brief.reset_for_tests()
    monkeypatch.setattr(decide, "is_active", lambda use: False)
    monkeypatch.setattr(decide, "outcome", lambda *a, **k: pytest.fail("no call while off"))
    assert message_brief.start(1, 2, "hello there") is False
    message_brief.note(1, 2, "mode", "TALK")  # no brief → ignored, no error


def test_notes_before_and_after_clef_answers_are_both_compared(brief):
    state, gate = brief
    assert message_brief.start(10, 20, "explain how raft consensus works") is True
    message_brief.note(10, 20, "mode", "TALK")  # BEFORE Clef answers → held
    assert state["compared"] == []
    gate.set()
    assert _wait_for(lambda: len(state["compared"]) == 1)
    message_brief.note(10, 20, "reminder", "no")  # AFTER → compared at once
    assert state["compared"] == [("mode", "REASON", "TALK"), ("reminder", "no", "no")]
    assert state["calls"] == 1, "ONE Clef call per message"
    assert set(state["qids"]) == {"mode", "depth", "system_intent", "space_intent", "reminder", "settings_intent"}


def test_one_comparison_per_question_per_message(brief):
    state, gate = brief
    gate.set()
    message_brief.start(1, 1, "remind me tomorrow at 9 to call the bank")
    assert _wait_for(lambda: state["calls"] == 1)
    message_brief.note(1, 1, "depth", "quick")
    message_brief.note(1, 1, "depth", "deep")  # a second decision for the same message
    assert [c for c in state["compared"] if c[0] == "depth"] == [("depth", "deep", "quick")]


def test_a_message_is_briefed_once(brief):
    state, gate = brief
    gate.set()
    assert message_brief.start(5, 6, "hello") is True
    assert message_brief.start(5, 6, "hello") is False


def test_messages_without_ids_or_text_are_skipped(brief):
    assert message_brief.start(None, 1, "x") is False
    assert message_brief.start(1, None, "x") is False
    assert message_brief.start(1, 2, "   ") is False


def test_old_briefs_are_evicted(brief, monkeypatch):
    state, gate = brief
    gate.set()
    monkeypatch.setattr(message_brief, "MAX_BRIEFS", 3)
    for i in range(6):
        message_brief.start(1, i, f"message {i}")
    assert len(message_brief._briefs) <= 4


def test_start_never_raises(monkeypatch):
    message_brief.reset_for_tests()

    def boom(use):
        raise RuntimeError("config on fire")

    monkeypatch.setattr(decide, "is_active", boom)
    assert message_brief.start(1, 2, "x") is False


def test_start_from_a_coroutine_returns_immediately(brief):
    state, gate = brief

    async def main():
        assert message_brief.start(3, 4, "what's my disk usage") is True  # Clef still blocked
        return True

    assert asyncio.run(main()) is True
    gate.set()


# ── judges ───────────────────────────────────────────────────


@pytest.fixture
def judged(monkeypatch):
    calls = []
    monkeypatch.setattr(
        decide, "judge",
        lambda use, state, question, legacy, sample="": calls.append((use, question.id, question.type, legacy)),
    )
    return calls


def test_each_judge_names_its_use_and_maps_the_legacy_answer(judged):
    judges.confirm_reply("да, давай", "space switch work", "other")
    judges.reply_quality("what is 2+2", "4", True)
    judges.continuation_state("Shall I deploy A or B?", "choice")
    judges.action_safety("rm -rf /var/www", False)
    judges.goal_achieved("deploy the site", "1. action=deploy | result=ok", True)
    assert judged == [
        ("judge_confirm", "intent", "choice", "other"),
        ("judge_reply_quality", "adequate", "noul", "yes"),
        ("judge_continuation", "state", "choice", "choice"),
        ("judge_action_safety", "safe", "noul", "no"),
        ("judge_goal", "achieved", "noul", "yes"),
    ]


def test_judge_options_match_what_the_legacy_code_produces():
    from navig.core.continuation import classify_continuation_state

    legacy_states = {classify_continuation_state(t)[0] for t in ("", "Done.", "Pick A or B?", "Continuing…")}
    assert legacy_states <= set(judges.CONTINUATION), "a legacy state Clef cannot answer can never agree"
    assert set(judges.CONFIRM) == {"confirm", "cancel", "other"}


def test_every_judge_and_the_brief_are_registered_shadow_only():
    for uid in ("telegram_brief", "judge_confirm", "judge_reply_quality", "judge_continuation",
                "judge_action_safety", "judge_goal"):
        use = decide.USES[uid]
        assert use.shadow_only and use.default_mode == "shadow", uid


def test_a_shadow_only_use_cannot_be_switched_live(monkeypatch):
    monkeypatch.setattr(decide, "_section", lambda: {"enabled": True, "use": {"judge_confirm": "on"}})
    assert decide.settings().uses["judge_confirm"] == "shadow"


def test_judge_is_a_noop_while_inactive(monkeypatch):
    monkeypatch.setattr(decide, "is_active", lambda use: False)
    monkeypatch.setattr(decide, "outcome", lambda *a, **k: pytest.fail("no call while off"))
    decide.judge("judge_confirm", "x", decide.yesno("q", "?"), "yes")


def test_judge_from_sync_code_runs_off_the_caller_and_compares(monkeypatch):
    done = threading.Event()
    seen = {}
    monkeypatch.setattr(decide, "is_active", lambda use: True)
    monkeypatch.setattr(decide, "outcome", lambda state, qs, *, use, images=None: _outcome({"q": "yes"}, use))

    def fake_compare(out, qid, legacy, sample=""):
        seen.update(qid=qid, legacy=legacy, clef=out.get(qid).answer, thread=threading.current_thread().name)
        done.set()

    monkeypatch.setattr(decide, "compare", fake_compare)
    decide.judge("judge_confirm", "ok go", decide.yesno("q", "?"), "no")
    assert done.wait(5)
    assert seen["clef"] == "yes" and seen["legacy"] == "no"
    assert seen["thread"] != threading.current_thread().name


def test_judge_never_raises(monkeypatch):
    def boom(use):
        raise RuntimeError("x")

    monkeypatch.setattr(decide, "is_active", boom)
    decide.judge("judge_confirm", "x", decide.yesno("q", "?"), "yes")  # must not raise
