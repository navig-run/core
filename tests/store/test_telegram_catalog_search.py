"""A soft-deleted Telegram message must not resurface in search.

`mark_message_deleted` sets `tg_messages.deleted=1` but leaves the FTS row. The FTS
search path (`_search_fts`) used to query `tg_search` with no `deleted` filter — so a
deleted message kept appearing in `search()`, while the LIKE fallback (`_search_like`,
`… AND deleted = 0`) correctly hid it. `_search_fts` now excludes deleted messages by
their deterministic FTS rowid (`id*2`). Under the old code the first test fails (the
deleted message is still returned).
"""

from __future__ import annotations

import pytest

from navig.store.telegram_catalog import TelegramCatalogStore


@pytest.fixture
def store(tmp_path):
    return TelegramCatalogStore(db_path=tmp_path / "catalog.db")


def _hit_ids(results):
    return {(r["ref_kind"], r["ref_id"]) for r in results}


def test_deleted_message_drops_out_of_fts_search(store):
    assert store._has_fts()  # this test must exercise the FTS path, not the LIKE fallback
    mid = store.upsert_message(chat_id=10, message_id=1, text="secret pineapple plans")
    other = store.upsert_message(chat_id=10, message_id=2, text="pineapple smoothie recipe")

    before = _hit_ids(store.search("pineapple"))
    assert ("message", mid) in before and ("message", other) in before

    assert store.mark_message_deleted(chat_id=10, message_id=1) is True

    after = _hit_ids(store.search("pineapple"))
    assert ("message", mid) not in after  # deleted -> gone from FTS search
    assert ("message", other) in after    # the survivor is still found


def test_media_hit_is_not_hidden_by_the_deleted_message_filter(store):
    """The id*2 message-rowid filter must never exclude a media hit (rowid id*2+1)."""
    assert store._has_fts()
    # A message and a media row sharing the same numeric id would have FTS rowids
    # differing by 1 (even vs odd) — deleting the message must not drop the media.
    mid = store.upsert_message(chat_id=10, message_id=1, text="mango note")
    store._index_fts("media", mid, 10, "mango photo caption")  # media rowid == mid*2+1
    store.mark_message_deleted(chat_id=10, message_id=1)

    hits = _hit_ids(store.search("mango"))
    assert ("message", mid) not in hits   # deleted message gone
    assert ("media", mid) in hits         # media with the same id survives
