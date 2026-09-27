"""The catalog is the only surviving record of a deleted message — so it has to
be readable.

A soft delete has always kept the row (`mark_message_deleted` sets `deleted=1`),
and until now every read path filtered those rows out and nothing offered them
back: "what was deleted" was answerable solely by the Telegram DM that fires
once. These cover the three reads the DM, the deck and the CLI share.
"""

from __future__ import annotations

import pytest

from navig.store.telegram_catalog import TelegramCatalogStore


@pytest.fixture
def store(tmp_path):
    return TelegramCatalogStore(db_path=tmp_path / "catalog.db")


def _seed(store, chat_id, message_id, **kw):
    store.upsert_message(chat_id=chat_id, message_id=message_id, **kw)


# ── list_deleted ──────────────────────────────────────────────────────────────


def test_list_deleted_spans_rooms_and_carries_their_titles(store):
    store.upsert_room(1, type="business", title="Sam")
    store.upsert_room(2, type="business", title="Nora")
    _seed(store, 1, 10, text="see you at 9")
    _seed(store, 2, 10, text="ok")          # same message_id, different room
    _seed(store, 1, 11, text="still here")  # not deleted
    store.mark_message_deleted(1, 10)
    store.mark_message_deleted(2, 10)

    rows = store.list_deleted()
    assert [r["message_id"] for r in rows] == [10, 10]
    # The room title has to ride along: this list spans rooms, so a row that
    # cannot name its own chat is unreadable.
    assert {r["room_title"] for r in rows} == {"Sam", "Nora"}
    assert all(r["deleted"] is True for r in rows)
    assert 11 not in [r["message_id"] for r in rows]   # a live message is not "deleted"


def test_list_deleted_orders_by_insertion_not_message_id(store):
    """Ordering a CROSS-ROOM list by message_id interleaves chats arbitrarily —
    ids are per-chat counters, so a chat with big ids would always sort first."""
    _seed(store, 1, 9999, text="old chat, huge id")
    _seed(store, 2, 2, text="new chat, tiny id")
    store.mark_message_deleted(1, 9999)
    store.mark_message_deleted(2, 2)

    rows = store.list_deleted()
    assert [r["text"] for r in rows] == ["new chat, tiny id", "old chat, huge id"]


def test_list_deleted_filters_by_chat_and_caps_limit(store):
    for i in range(1, 6):
        _seed(store, 1, i, text=f"a{i}")
        _seed(store, 2, i, text=f"b{i}")
        store.mark_message_deleted(1, i)
        store.mark_message_deleted(2, i)

    assert len(store.list_deleted(chat_id=1)) == 5
    assert {r["chat_id"] for r in store.list_deleted(chat_id=1)} == {1}
    assert len(store.list_deleted(limit=3)) == 3
    assert len(store.list_deleted(limit=0)) == 1      # clamped up, never a crash
    assert len(store.list_deleted(limit=10_000)) == 10  # clamped down to what exists


def test_list_deleted_joins_the_media_descriptor(store):
    mid = store.upsert_media(1, message_id=7, file_id="F1", file_unique_id="u1", kind="voice")
    _seed(store, 1, 7, media_ref=mid, kind="voice")
    store.mark_message_deleted(1, 7)

    row = store.list_deleted()[0]
    assert (row["media"] or {})["kind"] == "voice"


# ── deleted_only / include_deleted on the room view ───────────────────────────


def test_list_messages_deleted_only_and_include(store):
    _seed(store, 1, 1, text="live")
    _seed(store, 1, 2, text="gone")
    store.mark_message_deleted(1, 2)

    assert [m["text"] for m in store.list_messages(1)] == ["live"]              # default
    assert [m["text"] for m in store.list_messages(1, deleted_only=True)] == ["gone"]
    assert {m["text"] for m in store.list_messages(1, include_deleted=True)} == {"live", "gone"}
    # deleted_only wins over include_deleted — one flag decides, no third state.
    assert [m["text"] for m in store.list_messages(1, include_deleted=True, deleted_only=True)] == ["gone"]


# ── the derived `content` field ───────────────────────────────────────────────


def test_content_is_derived_so_a_file_less_message_still_says_what_it_was(store):
    """A poll/location/story has no text and no media row. Without this it reads
    back blank everywhere and looks like a message NAVIG failed to capture."""
    _seed(store, 1, 1, kind="business", raw={"business": True, "content": "poll"})
    store.mark_message_deleted(1, 1)

    assert store.get_message_by_ref(1, 1)["content"] == "poll"
    assert store.list_deleted()[0]["content"] == "poll"
    assert store.list_messages(1, deleted_only=True)[0]["content"] == "poll"


def test_content_is_absent_for_an_ordinary_message_and_survives_a_corrupt_payload(store):
    _seed(store, 1, 1, text="hello", raw={"business": True})
    assert "content" not in store.get_message_by_ref(1, 1)

    # One bad blob must not take out a listing (the raw column is also written by
    # the regular ingest, which stores whole Telegram messages).
    store._write("UPDATE tg_messages SET raw_json = ? WHERE chat_id = 1", ("{not json",))
    assert store.get_message_by_ref(1, 1)["text"] == "hello"
    assert len(store.list_messages(1)) == 1


def test_raw_payload_itself_is_never_returned(store):
    """The regular ingest stores the WHOLE Telegram message in raw_json. Returning
    it would multiply every deck response for one short string."""
    _seed(store, 1, 1, text="hi", raw={"business": True, "content": "poll", "secret": "x" * 500})
    row = store.get_message_by_ref(1, 1)
    assert row["content"] == "poll"
    assert "raw" not in row and "secret" not in str(row)


# ── first_message_at ──────────────────────────────────────────────────────────


def test_first_message_at_is_none_for_an_unknown_chat(store):
    """This is what tells "deleted a message older than the watch" apart from
    "NAVIG never saw this chat at all" — the two read identically otherwise."""
    assert store.first_message_at(999) is None


def test_first_message_at_returns_the_oldest_record_time(store):
    _seed(store, 1, 1, text="first")
    _seed(store, 1, 2, text="second")
    first = store.first_message_at(1)
    assert first and first.endswith("Z")
    rows = store._read_all("SELECT created_at FROM tg_messages WHERE chat_id = 1 ORDER BY id")
    assert first == rows[0]["created_at"]
