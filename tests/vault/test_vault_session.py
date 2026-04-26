"""Tests for navig.vault.session — VaultSession and SessionStore."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from navig.vault.session import SessionStore, VaultSession


def _make_session(ttl: int = 300) -> VaultSession:
    return VaultSession(
        master_key=b"test-key",
        unlocked_at=datetime.now(timezone.utc),
        ttl_seconds=ttl,
    )


@pytest.fixture(autouse=True)
def clear_store():
    """Ensure SessionStore is clean before and after each test."""
    SessionStore.clear()
    yield
    SessionStore.clear()


class TestVaultSession:
    def test_not_expired_fresh(self):
        s = _make_session(ttl=300)
        assert s.is_expired() is False

    def test_expired_when_ttl_zero(self):
        s = _make_session(ttl=0)
        assert s.is_expired() is True

    def test_touch_resets_idle(self):
        s = _make_session(ttl=300)
        original = s.last_used
        time.sleep(0.01)
        s.touch()
        assert s.last_used > original

    def test_remaining_seconds_positive(self):
        s = _make_session(ttl=300)
        assert s.remaining_seconds() > 0

    def test_remaining_seconds_zero_when_expired(self):
        s = _make_session(ttl=0)
        assert s.remaining_seconds() == 0

    def test_ttl_display_expired(self):
        s = _make_session(ttl=0)
        assert s.ttl_display() == "expired"

    def test_ttl_display_seconds_only(self):
        s = _make_session(ttl=45)
        display = s.ttl_display()
        # Should show something like "45s" (might be slightly less due to construction time)
        assert "s" in display

    def test_ttl_display_minutes(self):
        s = _make_session(ttl=120)
        display = s.ttl_display()
        assert "m" in display


class TestSessionStore:
    def test_get_returns_none_when_empty(self):
        assert SessionStore.get() is None

    def test_set_and_get_returns_session(self):
        s = _make_session()
        SessionStore.set(s)
        result = SessionStore.get()
        assert result is not None
        assert result.master_key == b"test-key"

    def test_clear_removes_session(self):
        SessionStore.set(_make_session())
        SessionStore.clear()
        assert SessionStore.get() is None

    def test_is_unlocked_false_when_empty(self):
        assert SessionStore.is_unlocked() is False

    def test_is_unlocked_true_when_active(self):
        SessionStore.set(_make_session())
        assert SessionStore.is_unlocked() is True

    def test_get_clears_expired_session(self):
        SessionStore.set(_make_session(ttl=0))
        assert SessionStore.get() is None

    def test_status_locked_when_empty(self):
        status = SessionStore.status()
        assert status["locked"] is True
        assert status["ttl"] is None

    def test_status_unlocked_when_active(self):
        SessionStore.set(_make_session())
        status = SessionStore.status()
        assert status["locked"] is False
        assert "ttl" in status
        assert "unlocked_at" in status

    def test_status_locked_when_expired(self):
        SessionStore.set(_make_session(ttl=0))
        status = SessionStore.status()
        assert status["locked"] is True

    def test_get_touches_session(self):
        s = _make_session()
        SessionStore.set(s)
        before = s.last_used
        time.sleep(0.01)
        SessionStore.get()
        assert s.last_used >= before
