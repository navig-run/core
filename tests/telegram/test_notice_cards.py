"""The three time-based notices as cards: render, expand, return, close, dispatch.

A card press carries only ``cd:<prefix>:<action>:<key>``, so these cases also pin
the remembered payload: Open must still reveal the brief / full tip / failing
checks after the sender's process is gone.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime

import pytest

from navig.telegram import cards, notice_cards
from navig.telegram.cards import CLOSED_TEXT, Screen


class FakeChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def _api_call(self, method, payload):
        self.calls.append((method, dict(payload)))
        if method == "sendMessage":
            return {"message_id": 71, "chat": {"id": payload.get("chat_id")}}
        return {"ok": True}

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    def payload(self, method: str) -> dict:
        return next(payload for name, payload in self.calls if name == method)

    def buttons(self, method: str) -> list[dict]:
        return [
            button
            for row in self.payload(method)["reply_markup"]["inline_keyboard"]
            for button in row
        ]


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    # chat_tidy's daily ledger is not what these cases assert; keep it off the
    # real config dir and out of the send path's way.
    from navig.telegram import chat_tidy

    monkeypatch.setattr(chat_tidy, "routine", lambda kind="notice": nullcontext())


@pytest.fixture(autouse=True)
def _registered():
    """The prefixes are registered on import; re-assert so a sibling suite's
    registry reset cannot leave a card unanswered in this file."""
    notice_cards.register()
    yield
    notice_cards.register()


def _cq(data: str, *, chat: int = 42, message: int = 7, user: int = 42) -> dict:
    return {
        "id": "cb1",
        "data": data,
        "message": {"chat": {"id": chat}, "message_id": message},
        "from": {"id": user},
    }


NOW = datetime(2026, 10, 21, 7, 13)


def _greeting() -> tuple[Screen, object]:
    return notice_cards.greeting_card("NAVIG — Приветствие", "Доброе утро. Пора за работу.", now=NOW)


def _tip() -> tuple[Screen, object]:
    return notice_cards.tip_card(
        "NAVIG — А ты знал",
        "💡 **Server Monitoring**: Real-time health dashboard.\n"
        "Try: `navig host monitor show`",
        feature="host_monitor",
        now=NOW,
    )


def _outpost() -> tuple[Screen, object]:
    return notice_cards.outpost_card(
        "lighthouse",
        title="Outpost unhealthy",
        checked_at=NOW,
        checks=["ssh: connection refused", "disk: 96% used"],
        now=NOW,
    )


# ── render ──────────────────────────────────────────────────────────────────


def test_the_greeting_is_a_card_with_open_and_close():
    screen, context = _greeting()
    text, rows = cards.render(screen, context)

    assert "Приветствие" in text
    assert "Пора за работу" in text
    assert [button["text"] for button in rows[0]] == ["Open brief", "Close"]
    assert cards.parse(rows[0][0]["callback_data"])[:2] == ("ngr", "open")


def test_the_daily_tip_is_a_card_with_open_and_close():
    screen, context = _tip()
    text, rows = cards.render(screen, context)

    assert "А ты знал" in text
    assert "**" not in text, "card bodies are sent without parse_mode — no markdown markers"
    assert [button["text"] for button in rows[0]] == ["Open", "Close"]
    assert cards.parse(rows[0][0]["callback_data"])[:2] == ("ntp", "open")


def test_the_outpost_alert_is_a_card_with_view_and_close():
    screen, context = _outpost()
    text, rows = cards.render(screen, context)

    assert "Outpost unhealthy" in text
    assert "lighthouse" in text
    assert "2 check(s) failing" in text
    assert [button["text"] for button in rows[0]] == ["View", "Close"]
    assert cards.parse(rows[0][0]["callback_data"])[:2] == ("nop", "open")


def test_every_card_button_fits_telegrams_64_byte_callback_limit():
    for screen, context in (_greeting(), _tip(), _outpost()):
        _text, rows = cards.render(screen, context)
        for row in rows:
            for button in row:
                assert len(button["callback_data"].encode("utf-8")) <= 64


# ── expand / return / close ─────────────────────────────────────────────────


def _press(channel: FakeChannel, context, action: str) -> dict:
    data = cards.encode(context.prefix, action, context.key)
    assert asyncio.run(cards.dispatch(channel, _cq(data), data)) is True
    return channel.payload("editMessageText")


def test_open_reveals_todays_brief_and_back_returns_to_the_greeting():
    screen, context = _greeting()
    detail = str(notice_cards.recall(context.prefix, context.key)["detail"])
    assert detail
    channel = FakeChannel()

    opened = _press(channel, context, "open")
    assert detail.splitlines()[0] in opened["text"]
    assert [b["text"] for b in channel.buttons("editMessageText")] == ["Back", "Close"]

    channel.calls.clear()
    back = _press(channel, context, "back")
    assert back["text"] == screen.text()
    assert [b["text"] for b in channel.buttons("editMessageText")] == ["Open brief", "Close"]


def test_open_reveals_the_full_tip_and_its_example_command():
    _screen, context = _tip()
    channel = FakeChannel()

    opened = _press(channel, context, "open")

    assert "Real-time health dashboard" in opened["text"]
    assert "`navig host monitor show`" in opened["text"]


def test_view_shows_the_host_check_time_and_failing_checks():
    _screen, context = _outpost()
    channel = FakeChannel()

    opened = _press(channel, context, "view")

    assert "Host: lighthouse" in opened["text"]
    assert "Checked: 2026-10-21 07:13" in opened["text"]
    assert "ssh: connection refused" in opened["text"]
    assert "disk: 96% used" in opened["text"]


@pytest.mark.parametrize("builder, prefix", [
    (_greeting, "ngr"),
    (_tip, "ntp"),
    (_outpost, "nop"),
])
def test_close_collapses_every_one_of_them_in_place(builder, prefix):
    _screen, context = builder()
    channel = FakeChannel()
    data = cards.encode(prefix, "close", context.key)

    assert asyncio.run(cards.dispatch(channel, _cq(data), data)) is True
    payload = channel.payload("editMessageText")
    assert payload["text"] == CLOSED_TEXT
    assert payload["reply_markup"] == {"inline_keyboard": []}


def test_a_pruned_card_answers_honestly_instead_of_failing_silently():
    context = cards.Context("nop", "lighthouse-20200101T0000")
    channel = FakeChannel()
    data = cards.encode("nop", "open", context.key)

    assert asyncio.run(cards.dispatch(channel, _cq(data), data)) is True
    assert "no longer stored" in channel.payload("editMessageText")["text"]


def test_all_three_prefixes_register_with_core_and_do_not_collide():
    registered = cards.registered()
    assert registered["ngr"] == ""
    assert registered["ntp"] == ""
    assert registered["nop"] == ""
    from navig.gateway.channels import telegram_extensions as tx

    assert tx.extension_for_callback("cd:ngr:open:2026-10-21") == "core"
    assert tx.extension_for_callback("cd:nop:view:lighthouse-20261021T0713") == "core"


# ── the sender ──────────────────────────────────────────────────────────────


def test_the_outpost_sender_is_at_most_once_per_window():
    channel = FakeChannel()

    first = asyncio.run(notice_cards.report_outpost_unhealthy(
        "lighthouse",
        title="Outpost unhealthy",
        checks=["ssh: connection refused"],
        checked_at=datetime(2026, 10, 21, 9, 0),
        chat_id=42,
        channel=channel,
        now=datetime(2026, 10, 21, 9, 0),
    ))
    again = asyncio.run(notice_cards.report_outpost_unhealthy(
        "lighthouse",
        title="Outpost unhealthy",
        checks=["ssh: connection refused"],
        chat_id=42,
        channel=channel,
        now=datetime(2026, 10, 21, 10, 0),
    ))
    later = asyncio.run(notice_cards.report_outpost_unhealthy(
        "lighthouse",
        title="Outpost unhealthy",
        checks=["ssh: connection refused"],
        chat_id=42,
        channel=channel,
        now=datetime(2026, 10, 21, 16, 30),
    ))

    assert first["sent"] is True
    assert again == {"sent": False, "suppressed": True, "host": "lighthouse"}
    assert later["sent"] is True
    assert channel.methods().count("sendMessage") == 2


def test_the_outpost_sender_does_not_burn_the_window_without_a_chat(monkeypatch):
    from navig.telegram import daily_once as claim

    monkeypatch.setattr(
        "navig.messaging.notify_operator.resolve_operator_chat_id", lambda: None
    )

    receipt = asyncio.run(notice_cards.report_outpost_unhealthy(
        "lighthouse",
        chat_id=None,
        channel=FakeChannel(),
        now=datetime(2026, 10, 21, 9, 0),
    ))

    assert receipt["sent"] is False
    assert receipt["suppressed"] is False
    assert claim.last_claim("outpost:lighthouse") is None
