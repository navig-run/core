"""The Guarded-mode risk check (navig.agent.tool_guard) and its deck route.

The check may only ADD a prompt in NAVIG OS, so every way it can fail to answer must
come back as "no answer" (the OS then asks the user) — never as an empty risk list,
which would let the call run unjudged.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from navig.agent import decide, tool_guard


def _outcome(p_yes: dict[str, float], *, live: bool = True) -> decide.Outcome:
    answers = {
        qid: decide.Decision(answer="yes" if p >= 0.5 else "no", confidence=max(p, 1 - p), probs={"yes": p, "no": 1 - p})
        for qid, p in p_yes.items()
    }
    return decide.Outcome(use=tool_guard.USE_ID, answers=answers, live=live, min_confidence=0.5, latency_ms=12)


def _patch(monkeypatch, result):
    seen = {}

    async def fake_aoutcome(state, questions, *, use, images=None):
        seen["state"], seen["questions"], seen["use"] = state, [q.id for q in questions], use
        return result

    monkeypatch.setattr(decide, "aoutcome", fake_aoutcome)
    return seen


CALL = {"tool": "Bash", "command": "git push --force origin main", "project_directory": "/repo"}


def test_flags_each_risk_at_or_above_the_threshold(monkeypatch):
    seen = _patch(monkeypatch, _outcome({"irreversible": 0.91, "outside_workspace": 0.12, "external": 0.5}))
    verdict = asyncio.run(tool_guard.judge_tool_call(CALL))
    assert verdict == {
        "risks": ["irreversible", "external"],
        "probabilities": {"irreversible": 0.91, "outside_workspace": 0.12, "external": 0.5},
        "latency_ms": 12,
    }
    assert seen["use"] == "guard_tool_call"
    assert seen["questions"] == ["irreversible", "outside_workspace", "external"]
    assert "git push --force origin main" in seen["state"] and "Project directory: /repo" in seen["state"]


def test_a_clearly_safe_call_has_no_risks(monkeypatch):
    _patch(monkeypatch, _outcome({"irreversible": 0.03, "outside_workspace": 0.02, "external": 0.01}))
    assert asyncio.run(tool_guard.judge_tool_call(CALL))["risks"] == []


@pytest.mark.parametrize(
    "result",
    [
        None,  # off, no credential, timeout, daily cap …
        _outcome({"irreversible": 0.01, "outside_workspace": 0.01, "external": 0.01}, live=False),  # shadow
        _outcome({"irreversible": 0.01, "outside_workspace": 0.01}),  # partial answer
    ],
    ids=["no-answer", "shadow", "partial"],
)
def test_no_usable_answer_is_none_never_an_empty_risk_list(monkeypatch, result):
    _patch(monkeypatch, result)
    assert asyncio.run(tool_guard.judge_tool_call(CALL)) is None


def test_a_raising_decision_model_is_none(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(decide, "aoutcome", boom)
    assert asyncio.run(tool_guard.judge_tool_call(CALL)) is None


def test_long_arguments_are_clipped_before_they_leave_the_machine():
    text = tool_guard.describe_call({"tool": "mcp__x__send", "arguments": {"body": "a" * 20_000}})
    assert len(text) < tool_guard.MAX_ARGUMENTS_CHARS + 200
    assert text.endswith("…(clipped)")


def test_the_use_is_registered_live_by_default():
    use = decide.USES["guard_tool_call"]
    # Not compare-only: in shadow every call would come back "no answer" and prompt.
    assert use.default_mode == "on" and not use.shadow_only


# ── the deck route ──


class _Request:
    def __init__(self, body):
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _route(body):
    from navig.gateway.deck.routes.decider import handle_deck_decider_guard

    resp = asyncio.run(handle_deck_decider_guard(_Request(body)))
    return resp.status, json.loads(resp.text)


def test_route_reports_unchecked_when_there_is_no_answer(monkeypatch):
    _patch(monkeypatch, None)
    assert _route(CALL) == (200, {"ok": True, "checked": False})


def test_route_returns_the_verdict(monkeypatch):
    _patch(monkeypatch, _outcome({"irreversible": 0.9, "outside_workspace": 0.1, "external": 0.1}))
    status, body = _route(CALL)
    assert status == 200 and body["checked"] is True and body["risks"] == ["irreversible"]


@pytest.mark.parametrize("body", [ValueError("bad json"), [], {"command": "ls"}, {"tool": "  "}])
def test_route_rejects_a_malformed_request(body):
    status, payload = _route(body)
    assert status == 400 and payload["ok"] is False
