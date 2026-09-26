"""Auto-heal must work on a TelegramChannel, not only on an AutoHealMixin instance.

``/autoheal on`` is a documented, user-facing toggle. The automatic path it turns on never
ran, and nothing said so, because both of its gates are silent by construction::

    # telegram_commands.py — the failure detector
    if _heal_ctx is not None and hasattr(self, "_heal_failure"):   # <- was False, always

    # telegram_autoheal.py — the session-manager lookup one level down
    if hasattr(self, "_has_feature") and self._has_feature("sessions"):   # <- also False

``TelegramChannel`` does not inherit ``AutoHealMixin`` (runtime MRO is
``[TelegramChannel, object]``), so neither name existed on it. Every failure the detector
found was dropped, and ``_record_heal_event`` returned early too -- so ``/autoheal status``
could never show an event either.

The existing suite (``tests/agent/test_autoheal.py``) is green throughout, and always was: it
builds ``AutoHealMixin.__new__(AutoHealMixin)`` and exercises the mixin in isolation. A test
that constructs the class the code does NOT run under can only ever prove that class works.
These tests construct a ``TelegramChannel``.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.gateway.channels.telegram import TelegramChannel
from navig.gateway.channels.telegram_autoheal import FailureClass, FailureContext

pytestmark = pytest.mark.integration


def _channel():
    ch = TelegramChannel.__new__(TelegramChannel)
    ch.sent: list[tuple] = []
    ch.store = None

    async def send_message(chat_id, text, **kw):
        ch.sent.append((text, kw.get("keyboard")))
        return {"message_id": 1}

    async def _api_call(method, params=None, **kw):
        return {"ok": True, "result": {"message_id": 1}}

    ch.send_message = send_message
    ch._api_call = _api_call
    return ch


def _ctx(**over):
    base = dict(
        original_cmd="navig host list",
        chat_id=1,
        user_id=2,
        failure_class=FailureClass.SSH_AUTH_FAIL,
        stderr="Permission denied (publickey)",
        exit_code=255,
        host="web01",
    )
    base.update(over)
    return FailureContext(**base)


def test_the_channel_does_not_inherit_the_autoheal_mixin():
    """Pins the premise. If this changes, the explicit binding becomes redundant."""
    assert [c.__name__ for c in TelegramChannel.__mro__] == ["TelegramChannel", "object"]


def test_the_trigger_condition_in_the_failure_detector_is_true():
    """Written exactly as telegram_commands.py evaluates it.

    This one line is the whole feature: while it was False, everything below it was dead code
    and no log line was produced.
    """
    ch = _channel()
    assert hasattr(ch, "_heal_failure"), (
        "the failure detector gates on this; while it is False, /autoheal on does nothing "
        "and nothing reports that"
    )


def test_the_session_manager_gate_is_reachable():
    """`_get_session_manager_safe` returned None unconditionally without `_has_feature`."""
    ch = _channel()
    assert hasattr(ch, "_has_feature")
    assert ch._has_feature("sessions"), (
        "'sessions' must be in _features; the heal-event recorder gives up without it"
    )
    assert not ch._has_feature("no-such-feature"), "the feature check must still say no"


def test_a_failure_produces_the_badge_and_the_action_keyboard():
    """The whole chain, driven the way the detector drives it."""
    ch = _channel()
    ctx = _ctx()
    asyncio.run(ch._heal_failure(ctx))

    assert len(ch.sent) == 1, f"expected one message, got {len(ch.sent)}"
    text, keyboard = ch.sent[0]
    assert "SSH_AUTH_FAIL" in text, f"the badge must name the failure class: {text[:80]!r}"
    assert keyboard, "the action buttons are the point — a bare error message is the old behaviour"
    assert len(keyboard) >= 1


def test_the_context_is_parked_so_the_buttons_have_something_to_act_on():
    """The keyboard's callbacks read `_pending_heal_ctx`; an unparked ctx is a dead button."""
    ch = _channel()
    ctx = _ctx()
    asyncio.run(ch._heal_failure(ctx))
    assert ctx.user_id in ch._pending_heal_ctx
    assert ch._pending_heal_ctx[ctx.user_id] is ctx


def test_the_state_self_initialises_without_an_init_call():
    """`_init_autoheal_state`'s docstring says to call it from ``TelegramChannel.__init__``.

    Nothing does. The mixin covers that with a lazy fallback, which is why binding alone is
    enough -- but the fallback is load-bearing, so it is pinned here rather than assumed.
    """
    ch = _channel()
    assert not hasattr(ch, "_active_heals"), "precondition: no init has run"
    asyncio.run(ch._heal_failure(_ctx()))
    assert hasattr(ch, "_active_heals") and hasattr(ch, "_pending_heal_ctx")


def test_the_heal_keyboard_buttons_are_dispatchable():
    """The buttons answered "⚠️ Auto-Heal not available" -- honest, and still dead."""
    ch = _channel()
    assert hasattr(ch, "_dispatch_heal_callback"), (
        "the heal_* callback branch falls back to 'Auto-Heal not available' without this"
    )
    for name in ("_run_explain", "_run_investigate", "_answer_callback"):
        assert hasattr(ch, name), f"{name} is reached from _dispatch_heal_callback"
