"""Every HitL channel must feed its send result into the shared health counter.

`HitLChannel.record_failure()` disables a channel after `_MAX_FAILURES`
consecutive failures, and `comms status` reports the counter. Matrix and
Telegram record it in `notify()`, `ask()` AND `choose()`. SMS recorded it only
in `notify()` — `ask`/`choose` discarded `_send_sms()`'s bool entirely — so a
Twilio account that rejected every question stayed `available` forever and kept
reporting `failures: 0`.

The empty string those two return is a REPLY limitation (SMS has no inbound
webhook), not a send result. Conflating the two is what hid the failure.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from navig.integrations.comms_router import SMSHitLChannel


def _sms() -> SMSHitLChannel:
    return SMSHitLChannel("AC_sid", "tok", "+15551230000", "+15559876543")


async def test_ask_records_a_failed_send():
    ch = _sms()
    ch._send_sms = AsyncMock(return_value=False)

    assert await ch.ask("Proceed with the deploy?") == ""  # reply limitation, unchanged
    assert ch._consecutive_failures == 1, "a rejected SMS must count as a failure"


async def test_choose_records_a_failed_send():
    ch = _sms()
    ch._send_sms = AsyncMock(return_value=False)

    assert await ch.choose("Which host?", ["web-1", "web-2"]) == ""
    assert ch._consecutive_failures == 1


async def test_repeated_ask_failures_disable_the_channel():
    """The consequence: without this, a dead Twilio account is offered forever."""
    ch = _sms()
    ch._send_sms = AsyncMock(return_value=False)

    for _ in range(ch._MAX_FAILURES):
        await ch.ask("Proceed?")

    assert ch.available is False, (
        "a channel that fails every send must stop being offered"
    )


async def test_a_successful_ask_resets_the_counter():
    ch = _sms()
    ch._send_sms = AsyncMock(return_value=False)
    await ch.ask("Proceed?")
    assert ch._consecutive_failures == 1

    ch._send_sms = AsyncMock(return_value=True)
    await ch.ask("Proceed?")

    assert ch._consecutive_failures == 0, "a delivered SMS clears the failure streak"
    assert ch.available is True


async def test_notify_still_reports_and_records():
    """notify() already recorded health; it must keep returning the real result."""
    ch = _sms()
    ch._send_sms = AsyncMock(return_value=True)
    assert await ch.notify("backup finished") is True
    assert ch._consecutive_failures == 0

    ch._send_sms = AsyncMock(return_value=False)
    assert await ch.notify("backup finished") is False
    assert ch._consecutive_failures == 1


@pytest.mark.parametrize("method", ["ask", "choose"])
async def test_send_is_still_attempted(method: str):
    """Health accounting must not have replaced the send itself."""
    ch = _sms()
    ch._send_sms = AsyncMock(return_value=True)

    if method == "ask":
        await ch.ask("Proceed?")
    else:
        await ch.choose("Which?", ["a", "b"])

    ch._send_sms.assert_awaited_once()
