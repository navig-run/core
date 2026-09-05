"""A double-tap on ⏩ must not run the conversion twice.

Telegram issues a NEW callback_query id for every press, so `_answered_callback_ids` —
which dedupes by that id — cannot see a repeat press as a repeat. Without a separate
guard, two taps download the file twice, run ffmpeg twice (up to 300s each) and send two
identical tracks back.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.gateway.channels.telegram_keyboards import CallbackHandler

pytestmark = pytest.mark.integration


class _Channel:
    """Minimal stand-in: records conversions and blocks until released."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.messages: list[str] = []
        self.gate = asyncio.Event()

    async def _edit_audio_and_reply(self, chat_id, file_id, mode, meta=None, reply_to_message_id=None):
        self.calls.append((chat_id, file_id, mode))
        await self.gate.wait()          # hold it "in flight"
        return True, "done"

    async def send_message(self, chat_id, text, **kw):
        self.messages.append(text)
        return {"message_id": 1}

    async def _api_call(self, *a, **kw):
        return {"ok": True}


def _handler(channel):
    h = CallbackHandler.__new__(CallbackHandler)   # skip __init__'s store wiring
    h.channel = channel
    h._answered_callback_ids = set()
    h._audio_jobs_inflight = set()
    h.answers = []

    async def _answer(cb_id, text="", show_alert=False):
        h.answers.append(text)

    h._answer = _answer
    return h


def _seed_cache(short_id: str) -> None:
    from navig.gateway.channels.telegram_voice import _af_cache

    _af_cache[short_id] = {"file_id": "FILEID", "is_speech": False, "title": "t", "file_size": 1024}


async def _press(h, short_id, action="speed"):
    await h._handle_audio_file_callback(
        cb_id=f"cb-{id(object())}",
        cb_data=f"audmsg:{action}:{short_id}",
        chat_id=42,
        message_id=7,
        user_id=9,
    )


def test_a_second_press_while_converting_does_not_start_a_second_conversion():
    async def run():
        ch = _Channel()
        h = _handler(ch)
        _seed_cache("sid1")

        first = asyncio.create_task(_press(h, "sid1"))
        await asyncio.sleep(0.05)          # let the first reach the in-flight state
        # ⚠ Bounded. Without the debounce the second press does not return — it starts a
        # real conversion and blocks on the same gate the first is holding. Awaited bare,
        # that is a DEADLOCK: the test hangs instead of failing, which reads as a stuck
        # suite rather than a missing guard.
        try:
            await asyncio.wait_for(_press(h, "sid1"), timeout=2)
        except asyncio.TimeoutError:
            ch.gate.set()
            await first
            pytest.fail("the second press blocked — it began a duplicate conversion instead of being refused")
        ch.gate.set()
        await first
        return ch, h

    ch, h = asyncio.run(run())
    assert len(ch.calls) == 1, f"the conversion ran {len(ch.calls)} times for one file"
    assert any("Already working" in a for a in h.answers), (
        "the second press must say something — a silently ignored button reads as broken"
    )


def test_the_slot_is_released_so_a_later_press_works():
    async def run():
        ch = _Channel()
        h = _handler(ch)
        _seed_cache("sid2")
        ch.gate.set()                      # complete immediately
        await _press(h, "sid2")
        await _press(h, "sid2")            # a fresh request after the first finished
        return ch, h

    ch, h = asyncio.run(run())
    assert len(ch.calls) == 2, "a press after the previous one finished must be honoured"
    assert not h._audio_jobs_inflight, "the in-flight set leaked an entry"


def test_a_raising_conversion_still_releases_the_slot():
    """Otherwise one failure wedges that button for the life of the process."""

    class _Boom(_Channel):
        async def _edit_audio_and_reply(self, *a, **kw):
            raise RuntimeError("ffmpeg exploded")

    async def run():
        ch = _Boom()
        h = _handler(ch)
        _seed_cache("sid3")
        with pytest.raises(RuntimeError):
            await _press(h, "sid3")
        return h

    h = asyncio.run(run())
    assert not h._audio_jobs_inflight, "a raising conversion left the button wedged"


def test_the_two_modes_are_independent():
    """⏩ and 🐢 on the same file are different requests and must both run."""

    async def run():
        ch = _Channel()
        h = _handler(ch)
        _seed_cache("sid4")
        first = asyncio.create_task(_press(h, "sid4", "speed"))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(_press(h, "sid4", "slowed"))
        await asyncio.sleep(0.05)
        ch.gate.set()
        await asyncio.gather(first, second)
        return ch

    ch = asyncio.run(run())
    assert {c[2] for c in ch.calls} == {"speed", "slowed"}
