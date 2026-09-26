"""A proactive nudge that ASKS something must ship the way to answer it.

Measured on the operator's bot::

    NAVIG — Reminder
      Тихая минута — прогнать проверку состояния, пока тебя нет?   13:25
    да                                                              13:27
      Хорошо, в чём помощь?                                         13:27

The nudge is delivered as a one-way push `Notification`. Nothing anywhere records
that a question is outstanding — `_record_send` stores only an event type, for
cooldown counting — so a plain "yes" reaches the ordinary chat handler, which knows
of no pending question and starts a fresh conversation.

It is the same gap behind 50 `approval_expired` incidents for "Remediate health
issues": the operator's answers never reach the thing that asked.

The fix reuses machinery that already exists rather than adding intent parsing:
`Notification.keyboard` (Telegram inline format) plus the `slash:` callback prefix,
which dispatches a bot command. So "yes" becomes a button that runs `/status`.
"""

from __future__ import annotations

from navig.agent.proactive.engagement import EngagementAction, EngagementResult


def test_the_idle_nudge_declares_the_command_that_answers_it() -> None:
    """Without this the delivery layer has nothing to build a button from."""
    import inspect

    from navig.agent.proactive import engagement as eng

    src = inspect.getsource(eng.EngagementCoordinator._evaluate_idle_nudge)
    assert "suggested_command" in src, (
        "the idle nudge asks a question but carries no command to answer it — "
        "delivery cannot offer a button, so a 'yes' reply goes nowhere"
    )


def test_the_button_label_is_localised_in_every_shipped_locale() -> None:
    """A Russian nudge under an English button is the bug one layer down."""
    import json
    from pathlib import Path

    import navig

    loc = Path(navig.__file__).parent / "locales"
    for lang in ("en", "ru", "fr"):
        p = loc / f"{lang}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        val = d.get("engagement.nudge_yes")
        assert val and val.strip(), f"{lang}.json is missing engagement.nudge_yes"


def test_the_result_shape_still_carries_metadata() -> None:
    """Anti-vacuity floor: if metadata stopped existing the checks above are moot."""
    r = EngagementResult(action=EngagementAction.IDLE_NUDGE, message="x", metadata={"a": 1})
    assert r.metadata == {"a": 1}


# NOTE: the button-shape assertions that used to live here re-implemented the
# delivery branch inline ("mirror of the delivery branch"), so they could pass
# while the shipped code was broken — the exact class this suite exists to catch.
# They are replaced by tests/agent/test_nudge_button_dispatches.py, which drives
# the real `_engagement_tick` and `_send_notification`, and additionally pins that
# the command the button ships can actually be dispatched.
