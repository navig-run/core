"""A double-tap on an LLM button must not answer twice.

Telegram issues a NEW callback_query id for every press, so ``_answered_callback_ids`` --
which dedupes by that id and discards it at the end of the press -- cannot see a repeat press
as a repeat. Two callbacks POST a message rather than editing one in place, so a double-tap is
not a harmless repeat: it runs the model twice and puts two answers in the chat, at twice the
cost.

``_handle_new_ideas_callback`` is deliberately NOT guarded here: it only edits the reply markup,
so a second run overwrites the first with the same kind of thing. Guarding it would be a
different judgement (wasted spend) and belongs in its own change.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.gateway.channels.telegram_keyboards import CallbackHandler

pytestmark = pytest.mark.integration


class _Channel:
    """Records what reached the chat, and holds the model call open on demand."""

    def __init__(self):
        self.messages: list[str] = []
        self.gate = asyncio.Event()

    async def send_message(self, chat_id, text, **kw):
        self.messages.append(text)
        return {"message_id": 1}

    async def _keep_typing(self, chat_id):
        await asyncio.Event().wait()      # cancelled by the handler's finally

    async def _api_call(self, *a, **kw):
        return {"ok": True}


class _Store:
    """Enough of the callback store for the keyboard builder to run."""

    def get(self, key):
        return "the question"

    def put(self, *a, **kw):
        pass


def _handler(channel):
    h = CallbackHandler.__new__(CallbackHandler)   # skip __init__'s store wiring
    h.channel = channel
    h.store = _Store()
    h._answered_callback_ids = set()
    h._jobs_inflight = set()
    h.answers = []
    h.ai_calls = 0

    async def _answer(cb_id, text="", show_alert=False):
        h.answers.append(text)

    async def _get_ai_response(prompt, user_id):
        h.ai_calls += 1
        # Only the FIRST call is held open. A later one returns at once, so removing the
        # guard makes these tests FAIL on the counts rather than DEADLOCK on the gate --
        # a test that hangs reports a timeout, not the defect it was written to catch.
        if h.ai_calls == 1:
            await channel.gate.wait()
        return "an answer"

    h._answer = _answer
    h._get_ai_response = _get_ai_response
    return h


class _Entry:
    user_message = "u"
    ai_response = "a"


def test_single_flight_admits_one_and_refuses_the_twin():
    """The mechanism itself, before either caller uses it."""
    h = _handler(_Channel())

    async def run():
        seen = []
        async with h._single_flight(("k", 1)) as first:
            seen.append(first)
            async with h._single_flight(("k", 1)) as second:
                seen.append(second)
            async with h._single_flight(("k", 2)) as other_key:
                seen.append(other_key)
        async with h._single_flight(("k", 1)) as after:
            seen.append(after)
        return seen

    assert asyncio.run(run()) == [True, False, True, True], (
        "expected: admitted, twin refused, a DIFFERENT key admitted, and the key reusable "
        "once the first finished"
    )
    assert not h._jobs_inflight, "the in-flight set leaked an entry"


def test_single_flight_releases_when_the_body_raises():
    """A raising job must not wedge the button forever."""
    h = _handler(_Channel())

    async def run():
        try:
            async with h._single_flight(("k", 1)) as go:
                assert go
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        async with h._single_flight(("k", 1)) as again:
            return again

    assert asyncio.run(run()) is True, "the key stayed locked after a raising body"
    assert not h._jobs_inflight


def test_a_double_tap_on_dig_deeper_runs_the_model_once():
    ch = _Channel()
    h = _handler(ch)

    async def run():
        first = asyncio.create_task(
            h._handle_dig_deeper_callback(
                cb_id="c1", chat_id=1, user_id=2, cb_key="k", entry=_Entry()
            )
        )
        await asyncio.sleep(0)            # let it register as in flight
        await h._handle_dig_deeper_callback(
            cb_id="c2", chat_id=1, user_id=2, cb_key="k", entry=_Entry()
        )
        ch.gate.set()
        await first

    asyncio.run(run())
    assert h.ai_calls == 1, f"the model ran {h.ai_calls} times for one question"
    assert len(ch.messages) == 1, f"{len(ch.messages)} answers reached the chat, expected 1"
    assert any("Already going deeper" in a for a in h.answers), (
        f"the second tap must say so rather than doing nothing silently: {h.answers}"
    )
    assert not h._jobs_inflight, "the in-flight set leaked an entry"


def test_a_double_tap_on_ask_followup_runs_the_model_once():
    ch = _Channel()
    h = _handler(ch)

    async def run():
        first = asyncio.create_task(
            h._handle_ask_followup_callback(cb_id="c1", chat_id=1, user_id=2, followup_key="f")
        )
        await asyncio.sleep(0)
        await h._handle_ask_followup_callback(
            cb_id="c2", chat_id=1, user_id=2, followup_key="f"
        )
        ch.gate.set()
        await first

    asyncio.run(run())
    assert h.ai_calls == 1, f"the model ran {h.ai_calls} times for one follow-up"
    assert len(ch.messages) == 1, f"{len(ch.messages)} answers reached the chat, expected 1"
    assert not h._jobs_inflight


def test_two_different_users_are_not_blocked_by_each_other():
    """The key must describe the REQUEST, not just the button."""
    ch = _Channel()
    h = _handler(ch)

    async def run():
        a = asyncio.create_task(
            h._handle_dig_deeper_callback(
                cb_id="c1", chat_id=1, user_id=2, cb_key="k", entry=_Entry()
            )
        )
        b = asyncio.create_task(
            h._handle_dig_deeper_callback(
                cb_id="c2", chat_id=1, user_id=99, cb_key="k", entry=_Entry()
            )
        )
        await asyncio.sleep(0)
        ch.gate.set()
        await asyncio.gather(a, b)

    asyncio.run(run())
    assert h.ai_calls == 2, "a second USER asking the same thing must not be refused"


def test_a_failure_while_acknowledging_does_not_wedge_the_button():
    """The acknowledgement must be INSIDE the guard, not before it.

    The audio branch used to add the job, then ``await self._answer(...)``, and only then
    enter the try/finally that releases it. A network blip answering the callback -- which is
    an ordinary Telegram failure, not an exotic one -- left the key in the set forever, and
    every later press answered "Already working on that one…" for a conversion that was never
    running. Moving the acknowledgement inside the context manager closes that.
    """
    h = _handler(_Channel())

    async def boom(cb_id, text="", show_alert=False):
        raise ConnectionError("telegram unreachable")

    h._answer = boom

    async def run():
        try:
            async with h._single_flight(("k", 1)) as go:
                assert go
                await h._answer("c1", "working…")
        except ConnectionError:
            pass
        async with h._single_flight(("k", 1)) as again:
            return again

    assert asyncio.run(run()) is True, (
        "a failed acknowledgement left the key held — the button is wedged for good"
    )
    assert not h._jobs_inflight
