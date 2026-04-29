"""
Tests for navig.store.threads.ThreadStore — SQLite-backed conversation thread store.
"""

import pytest

from navig.store.threads import ThreadStore


@pytest.fixture
def store(tmp_path):
    s = ThreadStore(db_path=tmp_path / "threads.db")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# get_or_create
# ---------------------------------------------------------------------------


def test_get_or_create_creates_thread(store):
    thread = store.get_or_create("whatsapp", "chat-111")
    assert thread.id is not None
    assert thread.adapter == "whatsapp"
    assert thread.remote_conversation_id == "chat-111"
    assert thread.status == "open"


def test_get_or_create_returns_existing(store):
    t1 = store.get_or_create("telegram", "chat-222")
    t2 = store.get_or_create("telegram", "chat-222")
    assert t1.id == t2.id


def test_get_or_create_with_contact_alias(store):
    thread = store.get_or_create("slack", "chan-1", contact_alias="alice")
    assert thread.contact_alias == "alice"


def test_get_or_create_with_meta(store):
    thread = store.get_or_create("sms", "123", meta={"key": "value"})
    assert thread.meta == {"key": "value"}


def test_get_or_create_different_adapters_create_separate(store):
    t1 = store.get_or_create("whatsapp", "chat-x")
    t2 = store.get_or_create("telegram", "chat-x")
    assert t1.id != t2.id


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


def test_get_by_id_found(store):
    thread = store.get_or_create("whatsapp", "chat-333")
    fetched = store.get_by_id(thread.id)
    assert fetched is not None
    assert fetched.id == thread.id


def test_get_by_id_not_found(store):
    result = store.get_by_id(9999)
    assert result is None


# ---------------------------------------------------------------------------
# touch
# ---------------------------------------------------------------------------


def test_touch_updates_last_active(store):
    thread = store.get_or_create("wa", "chat-t")
    old_ts = thread.last_active
    import time
    time.sleep(0.01)
    store.touch(thread.id)
    updated = store.get_by_id(thread.id)
    assert updated.last_active >= old_ts


# ---------------------------------------------------------------------------
# close_thread / reopen_thread
# ---------------------------------------------------------------------------


def test_close_thread(store):
    thread = store.get_or_create("way", "chat-c")
    result = store.close_thread(thread.id)
    assert result is True
    closed = store.get_by_id(thread.id)
    assert closed.status == "closed"


def test_close_already_closed_returns_false(store):
    thread = store.get_or_create("way", "chat-cc")
    store.close_thread(thread.id)
    result = store.close_thread(thread.id)
    assert result is False


def test_reopen_thread(store):
    thread = store.get_or_create("way", "chat-r")
    store.close_thread(thread.id)
    result = store.reopen_thread(thread.id)
    assert result is True
    reopened = store.get_by_id(thread.id)
    assert reopened.status == "open"


def test_reopen_already_open_returns_false(store):
    thread = store.get_or_create("way", "chat-rr")
    result = store.reopen_thread(thread.id)
    assert result is False


# ---------------------------------------------------------------------------
# link_contact
# ---------------------------------------------------------------------------


def test_link_contact(store):
    thread = store.get_or_create("wa", "chat-lc")
    result = store.link_contact(thread.id, "bob")
    assert result is True
    updated = store.get_by_id(thread.id)
    assert updated.contact_alias == "bob"


# ---------------------------------------------------------------------------
# list_threads
# ---------------------------------------------------------------------------


def test_list_threads_all(store):
    store.get_or_create("wa", "chat-l1")
    store.get_or_create("wa", "chat-l2")
    threads = store.list_threads()
    assert len(threads) >= 2


def test_list_threads_filter_adapter(store):
    store.get_or_create("wa", "chat-fa1")
    store.get_or_create("tg", "chat-fa2")
    wa_threads = store.list_threads(adapter="wa")
    assert all(t.adapter == "wa" for t in wa_threads)


def test_list_threads_filter_status(store):
    t = store.get_or_create("wa", "chat-fs")
    store.close_thread(t.id)
    closed = store.list_threads(status="closed")
    assert all(t.status == "closed" for t in closed)


def test_list_threads_limit(store):
    for i in range(5):
        store.get_or_create("wa", f"chat-lim-{i}")
    threads = store.list_threads(limit=3)
    assert len(threads) <= 3


def test_list_threads_filter_contact_alias(store):
    t = store.get_or_create("wa", "chat-ca-test", contact_alias="zara")
    threads = store.list_threads(contact_alias="zara")
    assert any(th.id == t.id for th in threads)
