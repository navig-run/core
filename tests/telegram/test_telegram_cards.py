"""The card framework: rendering, in-place edits, close semantics and callback dispatch.

No network: a fake channel records the Bot API calls. The point of each case is a
behaviour the operator sees — the same message changing, the spinner always stopping,
a switched-off plugin never running, and a "reply with the reason" prompt accepting
exactly one reply.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from navig.telegram import cards
from navig.telegram.cards import (
    Action,
    CardButton,
    Context,
    Screen,
    result_screen,
)


class FakeChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def _api_call(self, method, payload):
        self.calls.append((method, dict(payload)))
        if method == "sendMessage":
            return {"message_id": 71}
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
def _clean_registry():
    saved = dict(cards._REGISTRY)
    cards._REGISTRY.clear()
    cards._PENDING.clear()
    cards._INFLIGHT.clear()
    yield
    cards._REGISTRY.clear()
    cards._REGISTRY.update(saved)
    cards._PENDING.clear()
    cards._INFLIGHT.clear()


def _cq(data: str, *, chat: int = 42, message: int = 7, user: int = 42) -> dict:
    return {
        "id": "cb1",
        "data": data,
        "message": {"chat": {"id": chat}, "message_id": message},
        "from": {"id": user},
    }


def _summary() -> Screen:
    return Screen(title="Probe", body="one line", kind=cards.SUMMARY)


def _detail() -> Screen:
    return Screen(
        title="Probe",
        body="full body",
        kind=cards.DETAIL,
        rows=((CardButton("Approve", action="approve"), CardButton("Reject", action="reject")),),
    )


# ── rendering ───────────────────────────────────────────────────────────────


def test_a_summary_card_offers_open_and_close():
    text, rows = cards.render(_summary(), Context("probe", "k1"))

    assert text == "Probe\n\none line"
    flat = [button for row in rows for button in row]
    assert [button["text"] for button in flat] == ["Open", "Close"]
    assert cards.parse(flat[0]["callback_data"]) == ("probe", "open", "k1")
    assert cards.parse(flat[1]["callback_data"]) == ("probe", "close", "k1")


def test_a_detail_card_offers_back_and_close_below_the_consumers_actions():
    _text, rows = cards.render(_detail(), Context("probe", "k1"))

    assert [[button["text"] for button in row] for row in rows] == [
        ["Approve", "Reject"],
        ["Back", "Close"],
    ]
    assert cards.parse(rows[0][0]["callback_data"]) == ("probe", "approve", "k1")


def test_a_result_card_only_offers_close():
    _text, rows = cards.render(result_screen("Done", "it worked"), Context("probe", "k1"))

    assert [[button["text"] for button in row] for row in rows] == [["Close"]]


def test_a_consumer_can_keep_its_own_raw_button():
    screen = Screen(
        title="Probe",
        kind=cards.DETAIL,
        rows=(({"text": "Open thread", "url": "https://mail.example/x"},),),
    )

    _text, rows = cards.render(screen, Context("probe", "k1"))

    assert rows[0][0] == {"text": "Open thread", "url": "https://mail.example/x"}


def test_a_view_label_is_configurable_for_the_same_open_action():
    screen = Screen(title="Mail", kind=cards.SUMMARY, open_label="View")

    _text, rows = cards.render(screen, Context("mail", "k1"))

    assert rows[0][0]["text"] == "View"
    assert cards.parse(rows[0][0]["callback_data"])[1] == "open"


def test_callback_data_over_telegrams_limit_is_refused_before_it_is_emitted():
    with pytest.raises(ValueError, match="Telegram allows 64"):
        cards.encode("probe", "approve", "x" * 80)


def test_an_unknown_screen_kind_is_a_construction_error():
    with pytest.raises(ValueError, match="unknown card kind"):
        Screen(title="Probe", kind="popup")


# ── transport ───────────────────────────────────────────────────────────────


def test_send_card_returns_the_message_and_carries_the_keyboard():
    channel = FakeChannel()

    sent = asyncio.run(
        cards.send_card(42, _summary(), Context("probe", "k1"), channel=channel)
    )

    assert sent["message_id"] == 71
    assert channel.methods() == ["sendMessage"]
    assert channel.payload("sendMessage")["chat_id"] == 42
    assert [b["text"] for b in channel.buttons("sendMessage")] == ["Open", "Close"]


def test_update_card_edits_the_same_message_in_place():
    channel = FakeChannel()

    assert asyncio.run(
        cards.update_card(42, 7, _detail(), Context("probe", "k1"), channel=channel)
    )
    assert channel.methods() == ["editMessageText"]
    payload = channel.payload("editMessageText")
    assert payload["chat_id"] == 42 and payload["message_id"] == 7
    assert "full body" in payload["text"]
    assert [b["text"] for b in channel.buttons("editMessageText")] == [
        "Approve", "Reject", "Back", "Close",
    ]


def test_close_card_collapses_to_one_line_and_drops_the_keyboard():
    channel = FakeChannel()

    assert asyncio.run(cards.close_card(42, 7, channel=channel))
    payload = channel.payload("editMessageText")
    assert payload["text"] == cards.CLOSED_TEXT
    assert payload["reply_markup"] == {"inline_keyboard": []}


# ── dispatch ────────────────────────────────────────────────────────────────


def test_a_known_press_answers_first_then_edits_the_card():
    seen: list[Action] = []

    async def handler(action):
        seen.append(action)
        return result_screen("Done", action.key)

    cards.register_card_action("probe", handler)
    channel = FakeChannel()

    assert asyncio.run(
        cards.dispatch(channel, _cq("cd:probe:approve:k1"), "cd:probe:approve:k1")
    )
    assert seen and (seen[0].prefix, seen[0].action, seen[0].key) == ("probe", "approve", "k1")
    assert channel.methods() == ["answerCallbackQuery", "editMessageText"]
    assert "k1" in channel.payload("editMessageText")["text"]


def test_an_unknown_prefix_answers_a_toast_instead_of_failing():
    channel = FakeChannel()

    assert asyncio.run(cards.dispatch(channel, _cq("cd:ghost:open:k1"), "cd:ghost:open:k1"))
    assert channel.methods() == ["answerCallbackQuery"]
    assert "no longer available" in channel.payload("answerCallbackQuery")["text"]


def test_a_press_that_is_not_a_card_is_not_claimed():
    channel = FakeChannel()

    assert asyncio.run(cards.dispatch(channel, _cq("mr:abc"), "mr:abc")) is False
    assert channel.calls == []


def test_a_truncated_card_payload_is_answered_not_left_spinning():
    channel = FakeChannel()

    assert asyncio.run(cards.dispatch(channel, _cq("cd:dist"), "cd:dist")) is True
    assert channel.methods() == ["answerCallbackQuery"]
    assert "no longer valid" in channel.payload("answerCallbackQuery")["text"]


def test_a_switched_off_module_never_reaches_its_handler(monkeypatch):
    ran: list[str] = []

    async def handler(action):
        ran.append(action.key)
        return None

    monkeypatch.setattr(cards, "_module_enabled", lambda module: False)
    cards.register_card_action("probe", handler, module="probe-mod")
    channel = FakeChannel()

    assert asyncio.run(cards.dispatch(channel, _cq("cd:probe:open:k1"), "cd:probe:open:k1"))
    assert ran == []
    assert "switched off" in channel.payload("answerCallbackQuery")["text"]
    assert "editMessageText" not in channel.methods()


def test_a_crashing_handler_leaves_an_honest_card(monkeypatch):
    async def handler(action):
        raise RuntimeError("plugin bug")

    cards.register_card_action("probe", handler)
    channel = FakeChannel()

    assert asyncio.run(cards.dispatch(channel, _cq("cd:probe:open:k1"), "cd:probe:open:k1"))
    assert channel.methods() == ["answerCallbackQuery", "editMessageText"]
    assert "did not work" in channel.payload("editMessageText")["text"]


def test_close_collapses_without_calling_the_handler():
    ran: list[Action] = []

    async def handler(action):
        ran.append(action)
        return result_screen("should not happen")

    cards.register_card_action("probe", handler)
    channel = FakeChannel()

    assert asyncio.run(cards.dispatch(channel, _cq("cd:probe:close:k1"), "cd:probe:close:k1"))
    assert ran == []
    assert channel.methods() == ["answerCallbackQuery", "editMessageText"]
    assert channel.payload("editMessageText")["text"] == cards.CLOSED_TEXT


def test_a_double_tap_runs_the_handler_once():
    """A second press while the first is still working answers instead of posting twice."""
    calls: list[str] = []
    gate = asyncio.Event()

    async def handler(action):
        calls.append(action.key)
        await gate.wait()
        return result_screen("Done")

    cards.register_card_action("probe", handler)
    channel = FakeChannel()

    async def run() -> None:
        first = asyncio.create_task(
            cards.dispatch(channel, _cq("cd:probe:approve:k1"), "cd:probe:approve:k1")
        )
        await asyncio.sleep(0.01)
        await cards.dispatch(channel, _cq("cd:probe:approve:k1"), "cd:probe:approve:k1")
        gate.set()
        await first

    asyncio.run(run())
    assert calls == ["k1"]
    assert any("Already working" in payload.get("text", "")
               for method, payload in channel.calls if method == "answerCallbackQuery")
    assert not cards._INFLIGHT


# ── pending text replies ────────────────────────────────────────────────────


def _message(text: str, *, chat: int = 42, user: int = 42) -> dict:
    return {"text": text, "chat": {"id": chat}, "from": {"id": user}}


def _pending_handler(seen: list[Action]):
    async def handler(action):
        seen.append(action)
        return result_screen("Recorded", action.text)

    return handler


def test_the_next_text_message_becomes_the_reason():
    seen: list[Action] = []
    cards.register_card_action("probe", _pending_handler(seen))
    channel = FakeChannel()
    action = Action(
        prefix="probe", action="reject", key="k1", channel=channel,
        chat_id=42, message_id=7, user_id=42,
    )

    cards.await_text_reply(action, "reason")
    assert cards.has_pending_reply(42, 42)
    assert asyncio.run(cards.consume_reply(channel, _message("too salesy"))) is True
    assert seen and seen[0].action == "reason" and seen[0].text == "too salesy"
    assert channel.payload("editMessageText")["message_id"] == 7
    assert not cards.has_pending_reply(42, 42)


def test_a_slash_command_cancels_the_prompt_and_runs_normally():
    seen: list[Action] = []
    cards.register_card_action("probe", _pending_handler(seen))
    channel = FakeChannel()
    cards.await_text_reply(
        Action(prefix="probe", action="reject", key="k1", chat_id=42, message_id=7, user_id=42)
    )

    assert asyncio.run(cards.consume_reply(channel, _message("/status"))) is False
    assert seen == []
    assert channel.calls == []
    assert not cards.has_pending_reply(42, 42)


def test_another_sender_cannot_fill_someone_elses_prompt():
    seen: list[Action] = []
    cards.register_card_action("probe", _pending_handler(seen))
    channel = FakeChannel()
    cards.await_text_reply(
        Action(prefix="probe", action="reject", key="k1", chat_id=42, message_id=7, user_id=42)
    )

    assert asyncio.run(cards.consume_reply(channel, _message("hello", user=99))) is False
    assert seen == []
    assert cards.has_pending_reply(42, 42), "the owner's prompt must still be waiting"


def test_an_expired_prompt_is_not_consumed(monkeypatch):
    seen: list[Action] = []
    cards.register_card_action("probe", _pending_handler(seen))
    channel = FakeChannel()
    cards.await_text_reply(
        Action(prefix="probe", action="reject", key="k1", chat_id=42, message_id=7, user_id=42),
        ttl_s=30,
    )
    pending = cards._PENDING[(42, 42)]
    pending.expires = 0.0

    assert asyncio.run(cards.consume_reply(channel, _message("late"))) is False
    assert seen == []


# ── registration + wiring ───────────────────────────────────────────────────


def test_registration_validates_the_prefix():
    async def handler(action):
        return None

    with pytest.raises(ValueError, match="alphanumeric"):
        cards.register_card_action("bad:prefix", handler)
    with pytest.raises(ValueError, match="too long"):
        cards.register_card_action("abcdefghi", handler)
    cards.register_card_action("probe", handler, module="probe-mod")
    assert cards.registered() == {"probe": "probe-mod"}


def test_allowed_updates_includes_callback_query():
    """A card only works if the bot keeps ASKING for button presses (the list is sticky)."""
    from navig.telegram.updates import ALLOWED_UPDATES

    assert "callback_query" in ALLOWED_UPDATES


def test_the_gateway_channel_routes_the_card_prefix():
    """The framework is inert unless the channel sends ``cd:`` presses to it."""
    from navig.gateway.channels.telegram import TelegramChannel

    source = Path(inspect.getsourcefile(TelegramChannel)).read_text(encoding="utf-8")
    assert f'cb_data.startswith("{cards.CALLBACK_PREFIX}")' in source
    assert "card_actions.dispatch(self, callback_query, cb_data)" in source


def test_the_card_prefix_is_claimed_by_a_locked_extension():
    """Core owns the plumbing, so a press is never stripped by the keyboard filter."""
    from navig.gateway.channels import telegram_extensions as tx

    assert tx.extension_for_callback("cd:dist:open:draft-1") == "core"
    core = tx.get("core")
    assert core is not None and core.locked
