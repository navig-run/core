"""The greeting and daily-tip senders: cards, and at most once per local day.

These drive the REAL engagement path (``TelegramNotifier._engagement_tick`` →
``notification.card`` → ``send_card``) with a fake channel and a fake
coordinator. The second notifier stands in for the second process that greeted
the operator four minutes after the first one: it must see the durable claim and
produce nothing.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta

import pytest

from navig.agent.proactive.engagement import EngagementAction, EngagementResult
from navig.gateway.notifications import Notification, TelegramNotifier
from navig.telegram import daily_once as claim
from navig.telegram import notice_cards


class FakeChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def _api_call(self, method, payload):
        self.calls.append((method, dict(payload)))
        if method in ("sendMessage", "sendRichMessage"):
            return {"message_id": 71, "chat": {"id": payload.get("chat_id")}}
        return {"ok": True}

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    def payload(self, method: str) -> dict:
        return next(payload for name, payload in self.calls if name == method)

    def body(self, method: str) -> str:
        """The body Telegram carries: the rich html when the card has one, else text."""
        payload = self.payload(method)
        return payload.get("text") or (payload.get("rich_message") or {}).get("html", "")

    def buttons(self, method: str) -> list[dict]:
        return [
            button
            for row in self.payload(method)["reply_markup"]["inline_keyboard"]
            for button in row
        ]


class Coordinator:
    def __init__(self, result: EngagementResult) -> None:
        self._result = result
        self.calls = 0

    def engagement_tick(self):
        self.calls += 1
        return self._result


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.core import i18n
    from navig.telegram import chat_tidy

    titles = {
        "engagement.title.greeting": "NAVIG — Приветствие",
        "engagement.title.capability_promo": "NAVIG — А ты знал",
    }
    monkeypatch.setattr(i18n, "t", lambda key, **fields: titles.get(key, key))
    monkeypatch.setattr(chat_tidy, "routine", lambda kind="notice": nullcontext())
    # Quiet hours / DND depend on the operator's real state file; irrelevant here.
    monkeypatch.setattr(TelegramNotifier, "_should_suppress", lambda self, n: False)
    notice_cards.register()


def _notifier(channel: FakeChannel | None = None) -> tuple[TelegramNotifier, FakeChannel]:
    channel = channel or FakeChannel()
    return TelegramNotifier(channel, 42), channel


def _tick(notifier: TelegramNotifier, result: EngagementResult) -> Notification | None:
    notifier._engagement = Coordinator(result)
    return asyncio.run(notifier._engagement_tick())


def _greeting_result(message: str = "Доброе утро. Пора за работу.") -> EngagementResult:
    return EngagementResult(
        action=EngagementAction.GREETING,
        message=message,
        priority=8,
        metadata={"trigger": "morning", "tod": "morning"},
    )


def _tip_result() -> EngagementResult:
    return EngagementResult(
        action=EngagementAction.CAPABILITY_PROMO,
        message=(
            "💡 **Server Monitoring**: Real-time health dashboard.\nTry: `navig host monitor show`"
        ),
        priority=5,
        metadata={"feature": "host_monitor"},
    )


def test_the_morning_greeting_goes_out_as_a_card_with_open_and_close():
    notifier, _channel = _notifier()

    notification = _tick(notifier, _greeting_result())

    assert notification is not None
    assert notification.card is not None
    screen, context = notification.card
    assert context.prefix == "ngr"
    assert "Приветствие" in screen.text()
    assert "Пора за работу" in screen.text()


def test_the_greeting_card_is_delivered_once_and_the_second_tick_is_silent():
    notifier, channel = _notifier()
    first = _tick(notifier, _greeting_result())
    assert first is not None

    assert asyncio.run(notifier._send_notification(first)) is True
    assert channel.methods() == ["sendRichMessage"]
    body = channel.body("sendRichMessage")
    assert "Приветствие" in body
    assert "Пора за работу" in body
    assert [b["text"] for b in channel.buttons("sendRichMessage")] == ["Open brief", "Close"]

    assert _tick(notifier, _greeting_result()) is None
    assert channel.methods() == ["sendRichMessage"], "the suppressed tick sent nothing"


def test_a_second_sender_process_cannot_greet_again():
    first, first_channel = _notifier()
    second, second_channel = _notifier()

    assert _tick(first, _greeting_result()) is not None
    assert _tick(second, _greeting_result()) is None
    assert first_channel.calls == []
    assert second_channel.calls == []


def test_the_same_key_is_free_again_the_next_day(monkeypatch):
    notifier, _channel = _notifier()
    assert _tick(notifier, _greeting_result()) is not None
    assert _tick(notifier, _greeting_result()) is None

    class Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206 — mirrors datetime.now
            # Relative, not a hard-coded date: this test was written on 21 Oct with
            # "tomorrow = 2026-10-22" and then failed ON 2026-10-22 — the claim key is
            # per local day, so a fixed "tomorrow" becomes "today" and the third tick
            # is correctly suppressed. A date-brittle test is not a regression report.
            return datetime.now(tz) + timedelta(days=1)

    monkeypatch.setattr(claim, "datetime", Tomorrow)
    assert _tick(notifier, _greeting_result()) is not None


def test_the_daily_tip_goes_out_as_a_card_with_its_example_command():
    notifier, channel = _notifier()

    notification = _tick(notifier, _tip_result())

    assert notification is not None and notification.card is not None
    _screen, context = notification.card
    assert context.prefix == "ntp"
    assert asyncio.run(notifier._send_notification(notification)) is True
    assert "**" not in channel.body("sendRichMessage")
    assert [b["text"] for b in channel.buttons("sendRichMessage")] == ["Open", "Close"]

    # Open is the action that carries the full tip and the command line.
    data = notification.card[1]
    callback = f"cd:ntp:open:{data.key}"
    from navig.telegram import cards

    asyncio.run(
        cards.dispatch(
            channel,
            {
                "id": "cb1",
                "data": callback,
                "message": {"chat": {"id": 42}, "message_id": 71},
                "from": {"id": 42},
            },
            callback,
        )
    )
    edited = channel.body("editMessageText")
    assert "Real-time health dashboard" in edited
    assert "navig host monitor show" in edited


def test_the_tip_is_suppressed_for_the_rest_of_the_day():
    notifier, channel = _notifier()

    assert _tick(notifier, _tip_result()) is not None
    assert _tick(notifier, _tip_result()) is None
    assert channel.calls == []


def test_the_greeting_and_the_tip_do_not_suppress_each_other():
    notifier, _channel = _notifier()

    greeting = _tick(notifier, _greeting_result())
    tip = _tick(notifier, _tip_result())

    assert greeting is not None and greeting.card is not None
    assert tip is not None and tip.card is not None
    assert greeting.card[1].prefix == "ngr"
    assert tip.card[1].prefix == "ntp"


def test_other_engagement_actions_stay_plain_notifications():
    notifier, _channel = _notifier()
    result = EngagementResult(
        action=EngagementAction.IDLE_NUDGE,
        message="Still there?",
        priority=2,
        metadata={"suggested_command": "status"},
    )

    notification = _tick(notifier, result)

    assert notification is not None
    assert notification.card is None
    assert notification.keyboard == [[{"text": "Yes, go ahead", "callback_data": "slash:status"}]]
