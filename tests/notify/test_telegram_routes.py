"""Per-type Telegram routing: a type can go to its own chat (so the operator can
give that chat its own sound), the daily sales summary lands silently, and a
broken route falls back to the DM — loudly — instead of losing the message."""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def tg(tmp_path, monkeypatch):
    """Isolated notify.db + a fake Bot API. No network, ever."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    assert str(store._db_path()).startswith(str(tmp_path)), store._db_path()

    from navig.messaging import notify_operator as no
    from navig.notify import telegram_routes as tr

    monkeypatch.setattr(tr, "_last_failure_note", {})

    class Fake:
        def __init__(self):
            self.routed: list[dict] = []  # sends to a routed chat
            self.dm: list[dict] = []  # sends to the DM (notify_operator)
            self.refuse: dict[int, dict] = {}  # chat_id -> Bot API error body
            self.chats: dict[int, dict] = {}  # chat_id -> getChat result
            self.member: dict = {"status": "administrator", "can_post_messages": True}

        def call(self, method, payload):
            cid = payload.get("chat_id")
            if method == "sendMessage":
                if cid in self.refuse:
                    return {"ok": False, **self.refuse[cid]}
                self.routed.append(payload)
                return {"ok": True, "result": {"message_id": 1, "chat": {"id": cid, "type": "channel"}}}
            if method == "getChat":
                if cid in self.chats:
                    return {"ok": True, "result": self.chats[cid]}
                return {"ok": False, "description": "Bad Request: chat not found"}
            if method == "getMe":
                return {"ok": True, "result": {"id": 42}}
            if method == "getChatMember":
                return {"ok": True, "result": self.member}
            raise AssertionError(f"unexpected Bot API call {method}")

        def notify_operator(self, text, **kw):
            self.dm.append({"text": text, **kw})
            return True

    fake = Fake()
    monkeypatch.setattr(tr, "_call", fake.call)
    monkeypatch.setattr(no, "notify_operator", fake.notify_operator)
    return tr, fake


MONEY = -1004466964318


def test_an_unrouted_type_goes_to_the_dm(tg):
    tr, fake = tg
    ok, detail = tr.deliver("sale", "💰 sale")
    assert (ok, detail) == (True, "sent")
    assert fake.routed == [] and fake.dm == [{"text": "💰 sale", "chat_id": None, "silent": False}]


def test_a_routed_type_goes_to_its_own_chat_and_rings(tg):
    tr, fake = tg
    tr.set_route("sale", MONEY, "Money Machine")
    ok, detail = tr.deliver("sale", "💰 sale")
    assert ok and detail == "sent to Money Machine"
    assert fake.dm == []
    assert fake.routed[0]["chat_id"] == MONEY
    assert "disable_notification" not in fake.routed[0], "a sale must ring"


def test_the_daily_sales_summary_is_silent_by_default(tg):
    """The cash-register sound has to mean 'a sale just happened'."""
    tr, fake = tg
    assert tr.is_silent("sales_digest") is True
    assert tr.is_silent("sale") is False
    tr.deliver("sales_digest", "📊 yesterday")
    assert fake.dm[-1]["silent"] is True

    tr.set_route("sales_digest", MONEY, "Money Machine")
    tr.deliver("sales_digest", "📊 yesterday's totals")
    assert fake.routed[-1]["disable_notification"] is True


def test_sound_can_be_overridden_and_reset(tg):
    tr, _fake = tg
    tr.set_silent("sales_digest", False)
    assert tr.is_silent("sales_digest") is False
    tr.set_silent("sale", True)
    assert tr.is_silent("sale") is True
    tr.set_silent("sales_digest", None)
    assert tr.is_silent("sales_digest") is True


def test_a_broken_route_falls_back_to_the_dm_loudly_and_is_recorded(tg):
    """The bot was removed from the channel: the sale must still arrive, with a
    note, and the broken route must show up in the deck feed."""
    tr, fake = tg
    from navig.notify import feed

    tr.set_route("sale", MONEY, "Money Machine")
    fake.refuse[MONEY] = {"description": "Forbidden: bot is not a member of the channel chat"}
    ok, detail = tr.deliver("sale", "💰 sale")

    assert ok is True and "delivered to your DM" in detail
    assert len(fake.dm) == 1
    assert fake.dm[0]["text"].startswith("💰 sale")
    assert "Money Machine" in fake.dm[0]["text"] and "not a member" in fake.dm[0]["text"]
    assert fake.dm[0]["silent"] is False, "a broken route is exactly when you need to hear it"
    items = feed.list_items(limit=10)
    assert any("Money Machine" in i["title"] for i in items)

    # A second failure within the hour does not spam the feed — but still delivers.
    tr.deliver("sale", "💰 sale 2")
    assert len(fake.dm) == 2
    assert sum("Money Machine" in i["title"] for i in feed.list_items(limit=10)) == 1


def test_a_supergroup_migration_is_followed_and_saved(tg):
    tr, fake = tg
    old, new = -4425457788, -1004425457788
    tr.set_route("reminder", old, "Reminders")
    fake.refuse[old] = {
        "description": "Bad Request: group chat was upgraded to a supergroup chat",
        "parameters": {"migrate_to_chat_id": new},
    }
    ok, detail = tr.deliver("reminder", "⏰ stand up")
    assert ok and detail == "sent to Reminders"
    assert fake.routed[-1]["chat_id"] == new and fake.dm == []
    assert tr.get_route("reminder").chat_id == new


def test_resolve_accepts_the_id_the_telegram_app_shows(tg):
    """The app shows a channel as 4466964318; the Bot API needs -1004466964318."""
    tr, fake = tg
    fake.chats[MONEY] = {"id": MONEY, "type": "channel", "title": "Money Machine"}
    for raw in ("4466964318", "-1004466964318", " 4466964318 "):
        c = tr.resolve_chat(raw)
        assert (c.chat_id, c.title, c.kind) == (MONEY, "Money Machine", "channel")


def test_resolve_refuses_a_channel_the_bot_cannot_post_in(tg):
    tr, fake = tg
    fake.chats[MONEY] = {"id": MONEY, "type": "channel", "title": "Money Machine"}
    fake.member = {"status": "member"}
    with pytest.raises(ValueError, match="Post messages"):
        tr.resolve_chat("4466964318")


def test_resolve_explains_an_invite_link_and_an_unknown_chat(tg):
    tr, _fake = tg
    with pytest.raises(ValueError, match="invite link"):
        tr.resolve_chat("https://t.me/+5d6jKhS2KRgzNjZk")
    with pytest.raises(ValueError, match="can't see that chat"):
        tr.resolve_chat("123456789")


def test_routes_reject_unknown_types(tg):
    tr, _fake = tg
    with pytest.raises(ValueError):
        tr.set_route("nope", MONEY)
    with pytest.raises(ValueError):
        tr.set_silent("nope", True)


def test_the_router_sends_a_routed_type_to_its_chat(tg, monkeypatch):
    """End to end through NotificationRouter: matrix → route → chat."""
    tr, fake = tg
    from navig.gateway import notifications
    from navig.notify import prefs
    from navig.notify.router import NotificationRouter

    prefs.set_setting("master_enabled", True)
    for ch in ("deck", "telegram"):
        prefs.set_cell("sale", ch, ch == "telegram")
    monkeypatch.setattr(notifications, "get_notification_manager", lambda: type("NM", (), {"get_channel": lambda self, c: None})())
    tr.set_route("sale", MONEY, "Money Machine")

    out = asyncio.run(NotificationRouter().dispatch("sale", "New sale", "€4.99"))
    assert out["channels"] == [{"channel": "telegram", "ok": True, "detail": "sent to Money Machine"}]
    assert fake.routed[0]["chat_id"] == MONEY and "<b>New sale</b>" in fake.routed[0]["text"]


def test_the_telegram_target_setting_is_honoured_for_unrouted_types(tg):
    """The settings page says 'Leave blank to use your bound chat' — a value there
    has to actually be used."""
    tr, fake = tg
    from navig.notify import prefs

    assert tr.default_chat_id() is None
    prefs.set_setting("target_telegram", "-1009999")
    assert tr.default_chat_id() == "-1009999"
    tr.deliver("reminder", "⏰", fallback_chat_id=tr.default_chat_id())
    assert fake.dm[-1]["chat_id"] == "-1009999"


def test_an_unreadable_store_still_delivers_to_the_dm(tg, monkeypatch):
    """A routing read that fails must mean 'the DM', never 'no delivery'."""
    tr, fake = tg

    def _boom():
        raise RuntimeError("no such table: notify_settings")

    monkeypatch.setattr(tr, "_conn", _boom)
    ok, detail = tr.deliver("sales_digest", "📊")
    assert (ok, detail) == (True, "sent")
    assert fake.dm[-1]["silent"] is True  # the type's default still applies


def test_a_buttons_keyboard_survives_routing(tg):
    """#1815 puts a one-tap 'Switch to a working model' button on key-rejected alerts;
    a routed chat and the DM fallback must both keep it."""
    tr, fake = tg
    kb = [[{"text": "Switch to a working model", "callback_data": "x"}]]
    tr.deliver("system_alert", "🚨 key rejected", keyboard=kb)
    assert fake.dm[-1]["keyboard"] == kb

    tr.set_route("system_alert", MONEY, "Money Machine")
    tr.deliver("system_alert", "🚨 key rejected (second alert)", keyboard=kb)
    assert fake.routed[-1]["reply_markup"] == {"inline_keyboard": kb}

    # Unrouted, no keyboard: the call shape stays exactly what older callers expect.
    tr.set_route("system_alert", None)
    tr.deliver("system_alert", "🚨 plain")
    assert "keyboard" not in fake.dm[-1]


def test_five_identical_sale_cards_collapse_to_one(tg):
    """On 2026-10-26 five "💰 Sale — $19.99" cards arrived inside one minute.

    Whatever sent them five times, the operator must receive ONE card per payment:
    the delivery seam claims the rendered payload, so a retry loop, a second sender
    holding the same token, or a producer with a loop cannot ring five times.
    """
    tr, fake = tg
    card = "💰 Sale — $19.99\n\nb*@shop.io paid $19.99"

    first = tr.deliver("sale", card)
    rest = [tr.deliver("sale", card) for _ in range(4)]

    assert first == (True, "sent")
    assert all(outcome == (True, "duplicate collapsed") for outcome in rest)
    assert len(fake.dm) == 1


def test_a_collapsed_claim_is_given_back_when_nothing_was_delivered(tg, monkeypatch):
    """A send that never reached a chat must not swallow the real retry."""
    from navig.messaging import notify_operator as no

    tr, fake = tg
    monkeypatch.setattr(no, "notify_operator", lambda text, **kw: False)

    assert tr.deliver("sale", "💰 Sale — $19.99")[0] is False

    monkeypatch.setattr(no, "notify_operator", fake.notify_operator)
    ok, detail = tr.deliver("sale", "💰 Sale — $19.99")
    assert (ok, detail) == (True, "sent")
    assert len(fake.dm) == 1


def test_a_different_sale_still_arrives_inside_the_window(tg):
    """Collapsing is about the same words, never about the type: a second, real
    payment ($24.00) must ring even a minute after the first."""
    tr, fake = tg
    tr.deliver("sale", "💰 Sale — $19.99")
    tr.deliver("sale", "💰 Sale — $24.00")
    assert len(fake.dm) == 2

