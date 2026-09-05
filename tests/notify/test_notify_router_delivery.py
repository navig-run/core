"""`NotificationRouter._send_one` — the messaging-adapter delivery branch.

This branch (sms / discord / whatsapp) had no test coverage at all, which is why
it reported success by hand instead of reading the receipt:

    err = getattr(receipt, "error", None)
    return (not err), (err or status)

`DeliveryReceipt` carries an explicit `ok` field. Deriving success from the mere
absence of an error string means a receipt that failed without a message reads as
delivered, and a `None` receipt reads as "sent" because every getattr falls back
to its default. A dropped alert reported as delivered is the worst failure mode a
notification router has.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navig.messaging.adapter import DeliveryReceipt, DeliveryStatus
from navig.notify.router import NotificationRouter


def _router_with_adapter(adapter, target: str = "+15551234567"):
    """Route 'sms' to *adapter* with *target* configured."""
    registry = MagicMock()
    registry.get = MagicMock(side_effect=lambda name: adapter if name == "sms" else None)
    return (
        patch("navig.messaging.adapter_registry.get_adapter_registry", return_value=registry),
        patch("navig.notify.prefs.get_target", return_value=target),
    )


async def _send(adapter, target: str = "+15551234567"):
    reg_patch, target_patch = _router_with_adapter(adapter, target)
    router = NotificationRouter()
    with reg_patch, target_patch:
        return await router._send_one("sms", "test.type", "Title", "Body", "normal", {})


@pytest.mark.asyncio
async def test_successful_receipt_reports_sent():
    adapter = MagicMock()
    adapter.send_message = AsyncMock(return_value=DeliveryReceipt.success("mid-1"))
    ok, detail = await _send(adapter)
    assert ok is True
    assert detail == "sent"


@pytest.mark.asyncio
async def test_failure_receipt_reports_its_error():
    adapter = MagicMock()
    adapter.send_message = AsyncMock(return_value=DeliveryReceipt.failure("carrier rejected"))
    ok, detail = await _send(adapter)
    assert ok is False
    assert detail == "carrier rejected"


@pytest.mark.asyncio
async def test_failed_receipt_without_an_error_string_is_not_reported_as_delivered():
    """THE REGRESSION: `ok` is authoritative; absence of `error` is not success."""
    adapter = MagicMock()
    adapter.send_message = AsyncMock(
        return_value=DeliveryReceipt(ok=False, status=DeliveryStatus.FAILED)
    )
    ok, detail = await _send(adapter)
    assert ok is False, (
        "a receipt that says ok=False must never be reported as delivered just "
        "because it carried no error message"
    )
    assert detail == "failed"


@pytest.mark.asyncio
async def test_adapter_returning_no_receipt_is_a_failure_not_a_phantom_send():
    adapter = MagicMock()
    adapter.send_message = AsyncMock(return_value=None)
    ok, detail = await _send(adapter)
    assert ok is False, "a None receipt means nothing was delivered"
    assert "no receipt" in detail


@pytest.mark.asyncio
async def test_missing_target_short_circuits():
    adapter = MagicMock()
    adapter.send_message = AsyncMock(return_value=DeliveryReceipt.success("mid"))
    ok, detail = await _send(adapter, target="")
    assert (ok, detail) == (False, "no target configured")
    adapter.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_channel_is_rejected():
    router = NotificationRouter()
    ok, detail = await router._send_one("carrier-pigeon", "t", "T", "B", "normal", {})
    assert (ok, detail) == (False, "unknown channel")
