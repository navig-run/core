"""
navig.agent.judges — shadow comparisons of the decision model (Clef) against judgements
navig makes today with keyword lists, length rules or a model's self-assessment.

Each function takes the answer the existing logic ALREADY produced and hands Clef the same
question in the background. Nothing here changes what the caller does: every use is
shadow-only (:attr:`navig.agent.decide.Use.shadow_only`). The point is evidence:
``navig decider status`` shows how often Clef agrees, and ``navig decider review`` shows
where it does not, before any judge is allowed to decide.

All functions are fire-and-forget: they return immediately and never raise.
"""

from __future__ import annotations

from navig.agent import decide

#: Longest text handed to a judge. These are short decisions; every token is billed.
_MAX = 2_000

CONFIRM = {
    "confirm": "agrees to go ahead (yes, ok, do it — in any language)",
    "cancel": "declines or stops it (no, cancel, stop — in any language)",
    "other": "neither: a new request, a question, or something unrelated",
}

CONTINUATION = {
    "continue": "the assistant is partway through and can carry on by itself",
    "choice": "the assistant offers options and needs the user to pick one",
    "wait": "the assistant is waiting for information or input from the user",
    "blocked": "the assistant cannot proceed (an error, missing access, a hard stop)",
    "neutral": "a complete answer; nothing is pending",
}


def _clip(text: str) -> str:
    return (text or "")[:_MAX]


def confirm_reply(reply: str, pending: str, legacy: str) -> None:
    """A reply to a pending "run this?" prompt. *legacy*: ``confirm`` · ``cancel`` · ``other``."""
    decide.judge(
        "judge_confirm",
        _clip(f"The assistant asked: run this? → {pending}\nThe user replied: {reply}"),
        decide.choice("intent", "What does the user's reply mean?", CONFIRM),
        legacy,
        sample=reply,
    )


def reply_quality(request: str, reply: str, legacy_ok: bool) -> None:
    """Does *reply* actually answer *request*? *legacy_ok*: the retry rule's verdict."""
    decide.judge(
        "judge_reply_quality",
        _clip(f"Request: {request}\n\nReply: {reply}"),
        decide.yesno(
            "adequate",
            "Does the reply actually answer the request, without dodging, hedging or "
            "refusing for no reason?",
        ),
        "yes" if legacy_ok else "no",
        sample=request,
    )


def continuation_state(reply: str, legacy_state: str) -> None:
    """The assistant's own reply → continue · choice · wait · blocked · neutral."""
    decide.judge(
        "judge_continuation",
        _clip(reply),
        decide.choice("state", "Where does this assistant reply leave the conversation?", CONTINUATION),
        legacy_state,
        sample=reply,
    )


def action_safety(subject: str, legacy_safe: bool) -> None:
    """A proposed action (mission, tool call or claim) — safe to carry out?"""
    decide.judge(
        "judge_action_safety",
        _clip(subject),
        decide.yesno(
            "safe",
            "Is it safe to carry out this action as described — no data loss, no "
            "irreversible damage, nothing outside what was asked?",
        ),
        "yes" if legacy_safe else "no",
        sample=subject,
    )


def goal_achieved(goal: str, steps: str, legacy_achieved: bool) -> None:
    """After a multi-step task: was *goal* achieved, judged from the step results?"""
    decide.judge(
        "judge_goal",
        _clip(f"Goal: {goal}\n\nWhat was done:\n{steps}"),
        decide.yesno("achieved", "Do these results achieve the goal?"),
        "yes" if legacy_achieved else "no",
        sample=goal,
    )
