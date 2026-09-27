"""The digest reports business deletions the operator has not muted — nothing else.

Two leaks, both found by reading the shipped code against its own promise:

* **Muted chats.** `deletions mute <chat>` promises "stop announcing deletions from
  ONE chat (still recorded)". The instant path honoured it, but the digest counted
  every deleted row since the watermark — so a muted chat still landed in the card's
  count, under Show, and could even trigger a resume on its own.
* **Deletions the operator made.** The deck's delete route marks a catalog row
  deleted, and since `deleted_at` exists that stamp is set for ANY caller. A message
  the operator deleted in a group through the deck would have appeared in their
  BUSINESS digest as "1 deleted".

Muting is about announcing, not hiding: the browse surfaces still show a muted
chat's deletions, because the record is exactly what mute promises to keep.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import navig.telegram.business as b
import navig.telegram.deletions as d


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
    monkeypatch.setattr(no, "resolve_operator_chat_id", lambda: "777")
    b.remember_connection("bc1", owner_id=777, can_reply=True)
    return fake


def _ch():
    ch = MagicMock()
    ch.bot_token = "8490556839:ABC"
    ch._api_call = AsyncMock(return_value={"message_id": 1})
    return ch


def _store():
    from navig.store.telegram_catalog import TelegramCatalogStore
    return TelegramCatalogStore()


async def _business_deletion(ch, chat_id, mid, text="hi"):
    _store().upsert_message(chat_id, mid, sender_id=777, sender_name="operator",
                            date="1", text=text, kind="business", raw={"business": True})
    await b.handle_deleted_business_messages(
        ch, {"business_connection_id": "bc1", "message_ids": [mid], "chat": {"id": chat_id}})


def _operator_deleted_in_a_group(chat_id, mid):
    """What the deck's delete route does: a regular catalog row, marked deleted."""
    _store().upsert_message(chat_id, mid, sender_id=1, sender_name="someone",
                            date="2026-09-27T10:00:00Z", text="group chatter", kind="text")
    _store().mark_message_deleted(chat_id, mid)


def _cards(ch):
    return [c.args[1] for c in ch._api_call.call_args_list
            if c.args and c.args[0] == "sendMessage"]


# ── muted chats ───────────────────────────────────────────────────────────────


async def test_a_muted_chat_is_not_counted_in_the_digest(env):
    d.set_muted(556, True)
    ch = _ch()
    await _business_deletion(ch, 555, 1)
    await _business_deletion(ch, 556, 1)       # muted
    await _business_deletion(ch, 556, 2)       # muted

    res = await d.flush_digest(ch)
    assert res["sent"] and res["count"] == 1 and res["chats"] == 1
    assert "3" not in _cards(ch)[0]["reply_markup"]["inline_keyboard"][0][0]["text"]


async def test_a_digest_of_only_muted_deletions_is_not_sent(env):
    d.set_muted(556, True)
    ch = _ch()
    await _business_deletion(ch, 556, 1)
    res = await d.flush_digest(ch)
    assert res["sent"] is False and res["reason"] == "nothing_pending"
    assert _cards(ch) == []


async def test_resume_is_not_triggered_by_muted_deletions_alone(env, monkeypatch):
    d.set_muted(556, True)
    await _business_deletion(_ch(), 556, 1)
    armed: list = []
    monkeypatch.setattr(d, "arm_digest", lambda ch, delay=None: armed.append(delay))
    assert (await d.resume_pending(_ch()))["reason"] == "nothing_pending"
    assert armed == []


async def test_show_lists_exactly_what_the_card_counted(env):
    d.set_muted(556, True)
    ch = _ch()
    await _business_deletion(ch, 555, 1, text="visible one")
    await _business_deletion(ch, 556, 1, text="muted one")
    await d.flush_digest(ch)
    token = _cards(ch)[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    ch._api_call = AsyncMock(return_value={"message_id": 2})
    assert await d.handle_callback(ch, token, chat_id=777, message_id=5, user_id=777) == "Showed 1"
    detail = "\n".join(c["text"] for c in _cards(ch))
    assert "visible one" in detail and "muted one" not in detail


# ── deletions the operator made ───────────────────────────────────────────────


async def test_a_deck_deletion_in_a_group_never_reaches_the_business_digest(env):
    ch = _ch()
    _operator_deleted_in_a_group(-100555, 7)
    assert (await d.flush_digest(ch))["reason"] == "nothing_pending"

    await _business_deletion(ch, 555, 1)
    res = await d.flush_digest(ch)
    assert res["count"] == 1                    # the business one, not the group one


async def test_resume_is_not_triggered_by_a_deck_deletion(env, monkeypatch):
    _operator_deleted_in_a_group(-100555, 7)
    armed: list = []
    monkeypatch.setattr(d, "arm_digest", lambda ch, delay=None: armed.append(delay))
    assert (await d.resume_pending(_ch()))["reason"] == "nothing_pending"
    assert armed == []


# ── the browse surfaces keep the record ───────────────────────────────────────


async def test_the_business_record_still_shows_a_muted_chat(env):
    """Mute stops the ANNOUNCING. Hiding the record too would break the one thing
    mute promises to keep."""
    d.set_muted(556, True)
    await _business_deletion(_ch(), 556, 1, text="still on record")
    rows = _store().list_deleted(kind="business")
    assert [r["text"] for r in rows] == ["still on record"]


async def test_the_business_record_leaves_out_deck_deletions(env):
    await _business_deletion(_ch(), 555, 1, text="business")
    _operator_deleted_in_a_group(-100555, 7)
    assert [r["text"] for r in _store().list_deleted(kind="business")] == ["business"]
    # The unfiltered catalog view still has both — it is the whole record.
    assert {r["text"] for r in _store().list_deleted()} == {"business", "group chatter"}


def test_the_count_accepts_the_same_filters_as_the_list(env):
    s = _store()
    s.upsert_message(1, 1, text="a", kind="business")
    s.upsert_message(2, 1, text="b", kind="business")
    s.upsert_message(3, 1, text="c", kind="text")
    for chat in (1, 2, 3):
        s.mark_message_deleted(chat, 1)
    since = "2000-01-01T00:00:00.000Z"
    assert s.count_deleted_since(since)["messages"] == 3
    assert s.count_deleted_since(since, kind="business")["messages"] == 2
    assert s.count_deleted_since(since, kind="business", exclude_chats={2})["messages"] == 1
    assert s.count_deleted_since(since, exclude_chats=set())["messages"] == 3


# ── the card speaks local time ────────────────────────────────────────────────


async def test_the_digest_card_shows_local_time_not_utc(env, monkeypatch):
    """The card printed the raw UTC window start ("19:31 UTC"), two hours off an
    operator at UTC+2 — on the one line whose job is to say WHEN. Every other
    deletion surface already used local time via format_when."""
    import navig.telegram.business as biz

    rendered: list = []

    def fake_when(value):
        rendered.append(value)
        return "21:31"                       # what local time would render

    monkeypatch.setattr(biz, "format_when", fake_when)
    ch = _ch()
    await _business_deletion(ch, 555, 1)
    await d.flush_digest(ch)

    text = _cards(ch)[0]["text"]
    assert "21:31" in text
    assert "UTC" not in text
    assert rendered and rendered[0].endswith("Z")   # it was handed the stored UTC stamp


async def test_the_card_falls_back_to_labelled_utc_if_the_time_cannot_be_read(env, monkeypatch):
    """Better an honest "UTC" label than a blank where the time should be."""
    import navig.telegram.business as biz

    monkeypatch.setattr(biz, "format_when", lambda value: None)
    ch = _ch()
    await _business_deletion(ch, 555, 1)
    await d.flush_digest(ch)
    assert "UTC" in _cards(ch)[0]["text"]
