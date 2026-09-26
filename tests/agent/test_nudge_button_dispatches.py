"""The nudge's button must survive the whole chain, not just be built.

#1251 gave the idle nudge a button so a "yes" has somewhere to go. Every link in
that chain was unguarded, and the failure mode of each is silent:

    engagement._evaluate_idle_nudge   metadata["suggested_command"]
      -> notifications._engagement_tick   Notification(keyboard=[[{callback_data:
                                          "slash:<cmd>"}]])
      -> notifications._send_notification  send_message(..., keyboard=...)
      -> telegram.py callback branch       `slash:` -> _SLASH_REGISTRY -> handler

Rename the command, drop `keyboard=` in a refactor, or move the handler, and the
button still RENDERS — it just does nothing. The dispatcher's `if handler_fn:`
skips silently, which is the same shape as the approval handler that shipped with
buttons nothing ever built (#1265).

⚠ These drive the REAL functions. The first version of the #1251 test re-implemented
the delivery branch inline as a "mirror", which can pass while the shipped code is
broken — the class this file exists to prevent.
"""

from __future__ import annotations

import inspect

from navig.agent.proactive.engagement import (
    EngagementAction,
    EngagementResult,
)
from navig.gateway.notifications import Notification, TelegramNotifier


class _Channel:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, chat_id, message, keyboard=None, **kw):
        self.sent.append({"chat_id": chat_id, "message": message, "keyboard": keyboard})
        return {"message_id": 1}


class _Coordinator:
    def __init__(self, result) -> None:
        self._result = result

    def engagement_tick(self):
        return self._result


def _notifier() -> tuple[TelegramNotifier, _Channel]:
    ch = _Channel()
    n = TelegramNotifier(ch, 159901607)
    # ⚠ Don't gate on quiet hours/DND — same line as the two sibling notifier
    # suites (test_notifier_queue_delivery, test_notifier_send_honesty).
    #
    # Without it these tests read the WALL CLOCK: `_send_notification` asks the
    # real UserStateTracker whether it is quiet hours, and after 22:00 a
    # priority-2 notification is suppressed and never sent. The two tests below
    # then fail with "nothing was sent" and an IndexError — green by day, red by
    # night, on code that is correct either way. Every evening push got a red
    # gate from a file the pusher had not touched.
    #
    # The subject here is whether `keyboard=` survives the call, which has
    # nothing to do with the time of day.
    n._should_suppress = lambda _n: False
    return n, ch


def _nudge(**meta) -> EngagementResult:
    return EngagementResult(
        action=EngagementAction.IDLE_NUDGE,
        message="Quiet moment — want me to run a system check?",
        priority=2,
        metadata={"idle_hours": 1.0, **meta},
    )


# ── the command the button ships must be dispatchable ────────────────────────


def _suggested_command() -> str:
    """Read it off the real source, so a rename here is what the test sees."""
    src = inspect.getsource(
        __import__("navig.agent.proactive.engagement", fromlist=["x"])
    )
    import re

    m = re.search(r'"suggested_command":\s*"([a-z_]+)"', src)
    assert m, "the idle nudge no longer ships a suggested_command"
    return m.group(1)


def test_the_suggested_command_is_a_real_dispatchable_slash_command() -> None:
    """A button pointing at a command that does not resolve renders fine and does
    nothing — the dispatcher's `if handler_fn:` skips in silence."""
    from navig.gateway.channels.telegram_commands import (
        _SLASH_REGISTRY,
        TelegramCommandsMixin,
    )

    cmd = _suggested_command()
    entry = next((e for e in _SLASH_REGISTRY if e.command == cmd), None)
    assert entry is not None, (
        f"the nudge offers to run /{cmd}, which is not in the slash registry — "
        "the button would answer the tap and then do nothing"
    )
    assert entry.handler, f"/{cmd} is registered with no handler"
    # Resolved exactly the way telegram.py's `slash:` branch resolves it.
    fn = getattr(TelegramCommandsMixin, entry.handler, None)
    assert fn is not None, (
        f"/{cmd} names handler {entry.handler!r}, which does not exist on "
        "TelegramCommandsMixin — the dispatcher would find nothing to call"
    )


# ── delivery actually attaches the button ────────────────────────────────────


async def test_engagement_tick_attaches_a_button_for_the_real_command() -> None:
    n, _ = _notifier()
    cmd = _suggested_command()
    n._engagement = _Coordinator(_nudge(suggested_command=cmd))

    notif = await n._engagement_tick()

    assert notif is not None
    assert notif.keyboard, "a nudge that ASKS shipped with no way to answer"
    btn = notif.keyboard[0][0]
    assert btn["callback_data"] == f"slash:{cmd}", btn
    assert btn["text"].strip(), "an unlabelled button is an invisible tap target"
    # Telegram rejects callback_data over 64 bytes; the send would fail wholesale.
    assert len(btn["callback_data"].encode()) <= 64


async def test_a_nudge_without_a_command_gets_no_button() -> None:
    """Statements must not sprout buttons."""
    n, _ = _notifier()
    n._engagement = _Coordinator(_nudge())

    notif = await n._engagement_tick()

    assert notif is not None
    assert notif.keyboard is None


# ── the notifier must forward it to Telegram ─────────────────────────────────


async def test_send_notification_forwards_the_keyboard() -> None:
    """The link that makes the button real. Dropping `keyboard=` here leaves every
    prior assertion true and no button on screen."""
    n, ch = _notifier()
    kb = [[{"text": "Yes", "callback_data": "slash:status"}]]

    ok = await n._send_notification(
        Notification(type="routine", title="t", message="m", keyboard=kb)
    )

    assert ok is True
    assert ch.sent, "nothing was sent"
    assert ch.sent[0]["keyboard"] == kb, (
        "the notifier dropped Notification.keyboard, so the button never reaches "
        f"Telegram: {ch.sent[0]}"
    )


async def test_a_plain_notification_sends_no_keyboard() -> None:
    n, ch = _notifier()

    await n._send_notification(Notification(type="routine", title="t", message="m"))

    assert ch.sent[0]["keyboard"] is None
