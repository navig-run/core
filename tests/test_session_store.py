"""Tests for navig.gateway.session_store."""

from __future__ import annotations

import time

from navig.gateway.session_store import (
    OperatorContext,
    SessionKey,
    SessionStore,
    get_session_store,
    reset_session_store,
)

# ---------------------------------------------------------------------------
# SessionKey
# ---------------------------------------------------------------------------


class TestSessionKey:
    def test_hashable(self):
        k = SessionKey("telegram", "123")
        assert hash(k)  # no TypeError

    def test_str_with_thread(self):
        assert str(SessionKey("telegram", "42")) == "telegram:42"

    def test_str_no_thread(self):
        assert str(SessionKey("cli")) == "cli"

    def test_equality(self):
        assert SessionKey("web", "t1") == SessionKey("web", "t1")
        assert SessionKey("web", "t1") != SessionKey("web", "t2")


# ---------------------------------------------------------------------------
# OperatorContext
# ---------------------------------------------------------------------------


class TestOperatorContext:
    def test_get_set(self):
        ctx = OperatorContext(key=SessionKey("cli"))
        ctx.set("active_host", "prod")
        assert ctx.get("active_host") == "prod"

    def test_unset(self):
        ctx = OperatorContext(key=SessionKey("cli"))
        ctx.set("x", 1)
        ctx.unset("x")
        assert ctx.get("x") is None

    def test_default_for_missing_key(self):
        ctx = OperatorContext(key=SessionKey("cli"))
        assert ctx.get("missing", "fallback") == "fallback"

    def test_increment_turn(self):
        ctx = OperatorContext(key=SessionKey("cli"))
        assert ctx.increment_turn() == 1
        assert ctx.increment_turn() == 2

    def test_is_idle_false_for_new(self):
        ctx = OperatorContext(key=SessionKey("cli"))
        assert not ctx.is_idle(threshold=3600)

    def test_is_idle_true_for_old(self):
        ctx = OperatorContext(key=SessionKey("cli"))
        ctx.last_active = time.time() - 7200  # 2 hours ago
        assert ctx.is_idle(threshold=3600)

    def test_round_trip_dict(self):
        ctx = OperatorContext(key=SessionKey("telegram", "99"))
        ctx.set("foo", "bar")
        d = ctx.to_dict()
        restored = OperatorContext.from_dict(d)
        assert restored.key == ctx.key
        assert restored.get("foo") == "bar"


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------


class TestSessionStore:
    def test_get_or_create(self):
        store = SessionStore()
        key = SessionKey("cli")
        ctx = store.get_or_create(key)
        assert isinstance(ctx, OperatorContext)
        # Same object on second call
        assert store.get_or_create(key) is ctx

    def test_get_returns_none_for_missing(self):
        store = SessionStore()
        assert store.get(SessionKey("web", "x")) is None

    def test_touch_updates_last_active(self):
        store = SessionStore()
        key = SessionKey("cli")
        ctx = store.get_or_create(key)
        old_ts = ctx.last_active
        time.sleep(0.01)
        store.touch(key)
        assert ctx.last_active > old_ts

    def test_update_merges_meta(self):
        store = SessionStore()
        key = SessionKey("cli")
        store.update(key, {"host": "prod"})
        ctx = store.get_or_create(key)
        assert ctx.get("host") == "prod"

    def test_remove(self):
        store = SessionStore()
        key = SessionKey("cli")
        store.get_or_create(key)
        removed = store.remove(key)
        assert removed is True
        assert store.get(key) is None

    def test_remove_missing_returns_false(self):
        store = SessionStore()
        assert store.remove(SessionKey("nonexistent")) is False

    def test_expire_idle(self):
        store = SessionStore()
        k1 = SessionKey("telegram", "1")
        k2 = SessionKey("telegram", "2")
        store.get_or_create(k1)
        ctx2 = store.get_or_create(k2)
        ctx2.last_active = time.time() - 7200  # idle
        count = store.expire_idle(threshold=3600)
        assert count == 1
        assert store.get(k2) is None
        assert store.get(k1) is not None

    def test_all_contexts(self):
        store = SessionStore()
        store.get_or_create(SessionKey("cli"))
        store.get_or_create(SessionKey("telegram", "5"))
        assert len(store.all_contexts()) == 2


# ---------------------------------------------------------------------------
# JSON persistence round-trip
# ---------------------------------------------------------------------------


class TestSessionStorePersistence:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "sessions.json"
        store = SessionStore(persist_path=path)
        key = SessionKey("telegram", "42")
        ctx = store.get_or_create(key)
        ctx.set("active_host", "prod")
        store.save()

        store2 = SessionStore(persist_path=path)
        ctx2 = store2.get(key)
        assert ctx2 is not None
        assert ctx2.get("active_host") == "prod"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestGetSessionStore:
    def setup_method(self):
        reset_session_store()

    def teardown_method(self):
        reset_session_store()

    def test_singleton(self):
        s1 = get_session_store()
        s2 = get_session_store()
        assert s1 is s2
