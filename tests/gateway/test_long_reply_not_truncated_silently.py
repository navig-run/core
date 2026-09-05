"""A long reply that only partly went out must not report success.

`send_message` hands any body over `MAX_MESSAGE_UTF16` to `_send_long`, which splits it
and sends the parts in order. It used to return `last` — the final part's result —
unconditionally, so the whole send was judged by one part:

* earlier parts rejected, last one lands -> a truthy dict, so every caller
  (`_send_notification` checks `if sent is None`) reports success while the reader has a
  reply with a **hole** in it;
* last part rejected after the others went out -> `None`, so a caller that retries
  sends the earlier parts **again**.

This is the same defect `_send_with_attachments` documents for media, at the other end
of the list — and it sits on the live reply path: every answer over 4096 UTF-16 units,
which is routine for document-grade replies. Sending several messages back to back is
also exactly what earns a 429, and `_api_call` returns None only after exhausting its
own retry budget, so the failure is persistent rather than a blip.

Harness matches `test_telegram_rich.py`: a bare channel with `_api_call` swapped out —
no session, no daemon.
"""

from __future__ import annotations

import pytest

from navig.gateway.channels.base import utf16_len
from navig.gateway.channels.telegram_html import MAX_MESSAGE_UTF16


def _channel(results):
    """A channel whose `_api_call` yields `results` in order (None = rejected)."""
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel.__new__(TelegramChannel)
    calls: list[dict] = []
    seq = list(results)

    async def fake_api(method, data):
        calls.append(dict(data))
        return seq.pop(0) if seq else {"message_id": len(calls)}

    ch._api_call = fake_api  # type: ignore[method-assign]
    return ch, calls


def _long_html() -> str:
    big = "<pre>" + "\n".join("y" * 90 for _ in range(200)) + "</pre>"
    assert utf16_len(big) > MAX_MESSAGE_UTF16, "premise: this body must split"
    return big


async def test_a_rejected_middle_part_is_not_reported_as_delivered(navig_log_capture):
    """The dangerous direction: the LAST part succeeding used to mask everything.

    navig_log_capture (not caplog): navig's loggers set propagate=False.
    """
    # part 1 lands, part 2 is rejected (and its no-parse_mode retry too)
    ch, calls = _channel([{"message_id": 1}, None, None])

    res = await ch.send_message(12345, _long_html(), parse_mode="HTML")

    assert len(calls) >= 2, "premise: it really did split and try more than one part"
    assert res is None, (
        "a partially delivered reply reported success — the caller cannot tell the "
        "reader got a message with a hole in it"
    )
    assert any("TRUNCATED" in m for m in navig_log_capture)


async def test_sending_stops_at_the_first_rejection():
    """A contiguous prefix reads as cut off; parts 1,3,4 reads as complete and is not.

    Two calls: the failed part plus its parse-mode-stripped retry — and nothing after.
    """
    ch, calls = _channel([None, None, {"message_id": 9}, {"message_id": 10}])

    res = await ch.send_message(12345, _long_html(), parse_mode="HTML")

    assert res is None
    assert len(calls) == 2, (
        f"kept sending after a rejection ({len(calls)} calls) — that punches a hole "
        "into the middle of the reply"
    )


async def test_a_fully_delivered_long_reply_still_returns_the_last_result():
    """The success contract is unchanged — this must not become fail-closed."""
    ch, calls = _channel([])  # every call succeeds

    res = await ch.send_message(12345, _long_html(), parse_mode="HTML")

    assert len(calls) > 1  # actually split
    assert res == {"message_id": len(calls)}


async def test_a_first_part_rejection_reports_failure():
    """Nothing went out, so None is unambiguous and a retry is safe."""
    ch, calls = _channel([None, None])

    res = await ch.send_message(12345, _long_html(), parse_mode="HTML")

    assert res is None
    assert len(calls) == 2  # the send plus its parse-mode-stripped retry


async def test_the_parse_mode_retry_still_rescues_a_part():
    """A part rejected for its HTML is resent without parse_mode; that must still
    count as delivered rather than tripping the new truncation path."""
    ch, calls = _channel([None, {"message_id": 5}])  # first fails, retry lands

    res = await ch.send_message(12345, _long_html(), parse_mode="HTML")

    assert res is not None, "the parse-mode retry rescued the part; not a truncation"
    assert "parse_mode" not in calls[1], "the retry drops parse_mode"


@pytest.mark.parametrize(
    "mode,seq",
    [
        # HTML: the first send AND its parse-mode-stripped retry are rejected.
        ("HTML", [None, None]),
        # Plain: no retry exists, so one rejection is enough.
        (None, [None]),
    ],
)
async def test_later_parts_succeeding_does_not_mask_an_early_rejection(mode, seq):
    """HTML splits tag-safely, plain splits newline-aligned — both must report the
    failure. Every part AFTER the rejected one would succeed here (the fake falls
    through to success once its script runs out), which is precisely the shape that
    used to return a truthy `last` and hide the gap."""
    ch, calls = _channel(seq)

    res = await ch.send_message(12345, _long_html(), parse_mode=mode)

    assert res is None, (
        "later parts landing masked the rejected one — the caller sees success for a "
        "reply the reader received incomplete"
    )
    assert len(calls) == len(seq), "it must stop, not carry on to the parts that work"


# ── the other chunked sender: the /format command ─────────────────────────────


async def test_format_command_stops_at_a_rejected_chunk():
    """`/format` runs its own chunk loop, separate from `_send_long`.

    Nothing consumes its return (it is a command handler), so the phantom-success
    half does not apply here — but continuing past a rejected chunk still delivers
    1, 3, 4, i.e. output that reads as complete and is missing its middle.
    """
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel.__new__(TelegramChannel)
    sent: list[str] = []
    results = [{"message_id": 1}, None]  # chunk 2 rejected; later chunks would land

    async def fake_send(chat_id, text, **kwargs):
        sent.append(text)
        return results.pop(0) if results else {"message_id": len(sent)}

    ch.send_message = fake_send  # type: ignore[method-assign]

    from navig.gateway.channels.telegram_formatter import MarkdownFormatter

    body = "para\n\n" * 4000
    chunks = MarkdownFormatter().convert_chunked(body)
    assert len(chunks) > 3, "premise: this input really splits into several chunks"

    # Called off the MIXIN, not the channel: TelegramChannel's runtime MRO is
    # [TelegramChannel, object] — the command "mixins" are TYPE_CHECKING stubs bound
    # via functools.partial, so `_handle_format` is not an attribute of the channel.
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    await TelegramCommandsMixin._handle_format(ch, 12345, 1, "/format " + body)

    assert len(sent) == 2, (
        f"kept sending after a rejected chunk ({len(sent)}) — that leaves a gap in "
        "the middle of the output"
    )
