"""The digest card says WHO, not just how many.

Live feedback: "it works, but it doesn't say from whom it was deleted as preview".
The card read "удалено: 2 в 2 чатах" — the operator had to tap Show to learn even
which conversations. It now names each chat and whose messages went.

Names and counts only — never message text. The card is a notification preview
(it shows on a lock screen); content stays behind the Show button on purpose.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import navig.telegram.business as b
import navig.telegram.deletions as d

OWNER = 777


class _FakeCfg:
    def __init__(self) -> None:
        self.d: dict = {}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value, scope=None):  # noqa: A003
        self.d[key] = value

    def save(self, scope=None):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = _FakeCfg()
    fake.d[d.CFG_MODE] = "digest"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NAVIG_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(b, "_cfg", lambda: fake)
    monkeypatch.setattr(d, "_cfg", lambda: fake)
    monkeypatch.setattr(b.permissions, "_cfg", lambda: fake, raising=False)
    monkeypatch.setattr(b.permissions, "business_enabled", lambda: True)
    monkeypatch.setattr(d, "should_notify", lambda: (True, ""))
    import navig.messaging.notify_operator as no
    monkeypatch.setattr(no, "resolve_operator_chat_id", lambda: str(OWNER))
    b.remember_connection("bc1", owner_id=OWNER, can_reply=True)
    return fake


def _ch():
    ch = MagicMock()
    ch.bot_token = "8490556839:ABC"
    ch._api_call = AsyncMock(return_value={"message_id": 1})
    return ch


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore
    return TelegramCatalogStore()


async def _deleted(ch, chat_id, title, mid, *, sender, text="private words"):
    _store().upsert_room(chat_id, type="business", title=title)
    _store().upsert_message(chat_id, mid, sender_id=sender, sender_name="x", date="1",
                            text=text, kind="business", raw={"business": True})
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [mid],
             "chat": {"id": chat_id, "first_name": title}})


async def _card(ch) -> str:
    assert (await d.flush_digest(ch))["sent"]
    return [c.args[1] for c in ch._api_call.call_args_list
            if c.args and c.args[0] == "sendMessage"][0]["text"]


async def test_the_card_names_each_chat_and_whose_messages_went(env):
    """The exact live case: two chats, one deletion each, both the owner's own."""
    ch = _ch()
    await _deleted(ch, 429106811, "Yck 🧢", 1, sender=OWNER)
    await _deleted(ch, 981536938, "Принцесска", 1, sender=OWNER)
    text = await _card(ch)
    assert "• Yck 🧢 — 1 (from you)" in text
    assert "• Принцесска — 1 (from you)" in text


async def test_their_messages_are_named_without_a_from_you_tag(env):
    ch = _ch()
    await _deleted(ch, 555, "Elvira", 1, sender=555)
    await _deleted(ch, 555, "Elvira", 2, sender=555)
    assert "• Elvira — 2\n" in await _card(ch)


async def test_a_mixed_chat_says_how_many_were_yours(env):
    ch = _ch()
    await _deleted(ch, 555, "Elvira", 1, sender=555)
    await _deleted(ch, 555, "Elvira", 2, sender=OWNER)
    await _deleted(ch, 555, "Elvira", 3, sender=555)
    assert "• Elvira — 3 (1 from you)" in await _card(ch)


async def test_the_busiest_chat_comes_first(env):
    ch = _ch()
    await _deleted(ch, 1, "Quiet", 1, sender=1)
    for mid in (1, 2, 3):
        await _deleted(ch, 2, "Busy", mid, sender=2)
    text = await _card(ch)
    assert text.index("Busy") < text.index("Quiet")


async def test_a_busy_day_folds_into_more_after_five_chats(env):
    ch = _ch()
    for chat in range(1, 8):
        await _deleted(ch, chat, f"Chat{chat}", 1, sender=chat)
    text = await _card(ch)
    assert text.count("\n• ") == d.WHO_MAX_CHATS + 1       # five names + one "+N"
    assert "• +2 more" in text


async def test_the_card_never_carries_message_text(env):
    """It is a lock-screen preview. The words stay behind Show."""
    ch = _ch()
    await _deleted(ch, 555, "Elvira", 1, sender=555, text="the secret sentence")
    assert "the secret sentence" not in await _card(ch)


async def test_muted_and_deck_deletions_stay_out_of_the_names(env):
    """The names must add up to the number on the card — same scope as the count."""
    ch = _ch()
    d.set_muted(556, True)
    await _deleted(ch, 555, "Visible", 1, sender=555)
    await _deleted(ch, 556, "Muted", 1, sender=556)
    _store().upsert_message(-100, 7, sender_id=1, text="group", kind="text")
    _store().mark_message_deleted(-100, 7)
    text = await _card(ch)
    assert "Visible" in text and "Muted" not in text and "-100" not in text


async def test_a_chat_with_no_stored_title_falls_back_to_its_id(env):
    ch = _ch()
    _store().upsert_message(4242, 1, sender_id=4242, text="x", kind="business")
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [1], "chat": {"id": 4242}})
    assert "• 4242 — 1" in await _card(ch)


async def test_a_failing_preview_never_costs_the_card(env, monkeypatch):
    """The names are a courtesy on top of a card that already has its count and its
    Show button."""
    def boom(*a, **k):
        raise RuntimeError("db hiccup")

    ch = _ch()
    await _deleted(ch, 555, "Elvira", 1, sender=555)
    monkeypatch.setattr(type(_store()), "deleted_by_chat_since", boom)
    text = await _card(ch)
    assert "1 deleted" in text and "Elvira" not in text


def test_the_grouped_query_adds_up_to_the_count(env):
    s = _store()
    for chat, mid, sender in ((1, 1, 1), (1, 2, OWNER), (2, 1, 2)):
        s.upsert_message(chat, mid, sender_id=sender, text="t", kind="business")
        s.mark_message_deleted(chat, mid)
    since = "2000-01-01T00:00:00.000Z"
    rows = s.deleted_by_chat_since(since, owner_id=OWNER, kind="business")
    assert sum(r["count"] for r in rows) == s.count_deleted_since(since, kind="business")["messages"]
    assert {r["chat_id"]: r["mine"] for r in rows} == {1: 1, 2: 0}
    # An unknown owner counts nothing as "mine" rather than guessing.
    assert all(r["mine"] == 0 for r in s.deleted_by_chat_since(since, kind="business"))


def test_the_owner_is_found_from_the_registry_without_a_connection_id(env):
    """The digest has no update to read a connection id from. resolve_owner(None)
    skipped the registry and only checked allowed_users, so an owner recorded only
    in the registry was invisible and every row lost its "(yours)" tag."""
    assert b.primary_owner() == OWNER


def test_the_owner_falls_back_to_allowed_users(env):
    env.d.pop(b.CFG_CONNECTIONS, None)
    env.d["telegram"] = {"allowed_users": [159901607]}
    assert b.primary_owner() == 159901607


def test_no_owner_anywhere_is_none_not_a_guess(env):
    env.d.pop(b.CFG_CONNECTIONS, None)
    assert b.primary_owner() is None
