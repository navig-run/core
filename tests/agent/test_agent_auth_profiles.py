"""
Batch 37 — navig/agent/auth_profiles.py

Covers:
  AuthProfile: dataclass fields and defaults
  ProfileCooldown: is_on_cooldown(), record_failure() backoff calc, reset(), remaining_seconds()
  AuthProfilePool: next_available(), mark_failure(), mark_good(),
                   weighted rotation, all-on-cooldown returns None,
                   add_profile(), remove_profile(), status(), len(), healthy_count()
"""

from __future__ import annotations

import time

import pytest

from navig.agent.auth_profiles import AuthProfile, AuthProfilePool, ProfileCooldown


# ---------------------------------------------------------------------------
# AuthProfile
# ---------------------------------------------------------------------------

class TestAuthProfile:
    def test_required_fields(self):
        p = AuthProfile(name="primary", api_key="sk-abc")
        assert p.name == "primary"
        assert p.api_key == "sk-abc"

    def test_defaults(self):
        p = AuthProfile(name="x", api_key="y")
        assert p.provider == "openai"
        assert p.weight == 1
        assert p.extra == {}

    def test_custom_provider_and_weight(self):
        p = AuthProfile(name="x", api_key="y", provider="anthropic", weight=3)
        assert p.provider == "anthropic"
        assert p.weight == 3

    def test_extra_arbitrary(self):
        p = AuthProfile(name="x", api_key="y", extra={"org": "org_123"})
        assert p.extra["org"] == "org_123"


# ---------------------------------------------------------------------------
# ProfileCooldown
# ---------------------------------------------------------------------------

class TestProfileCooldown:
    def test_initial_not_on_cooldown(self):
        cd = ProfileCooldown()
        assert cd.is_on_cooldown() is False

    def test_remaining_zero_when_not_cooling(self):
        cd = ProfileCooldown()
        assert cd.remaining_seconds() == 0.0

    def test_record_failure_increments(self):
        cd = ProfileCooldown()
        cd.record_failure()
        assert cd.failure_count == 1

    def test_record_failure_sets_cooldown(self):
        cd = ProfileCooldown()
        cd.record_failure()
        # After 1 failure: base * 2^0 = 5.0s
        assert cd.cooldown_seconds == pytest.approx(5.0)

    def test_record_failure_exponential(self):
        cd = ProfileCooldown()
        cd.record_failure()
        cd.record_failure()
        # After 2nd failure: 5 * 2^1 = 10.0
        assert cd.cooldown_seconds == pytest.approx(10.0, rel=0.01)

    def test_record_failure_caps_at_max(self):
        cd = ProfileCooldown()
        for _ in range(20):
            cd.record_failure()
        assert cd.cooldown_seconds <= 300.0

    def test_after_failure_is_on_cooldown(self):
        cd = ProfileCooldown()
        cd.record_failure()
        assert cd.is_on_cooldown() is True

    def test_remaining_positive_after_failure(self):
        cd = ProfileCooldown()
        cd.record_failure()
        remaining = cd.remaining_seconds()
        assert remaining > 0.0

    def test_reset_clears_state(self):
        cd = ProfileCooldown()
        cd.record_failure()
        cd.reset()
        assert cd.failure_count == 0
        assert cd.cooldown_seconds == 0.0
        assert cd.is_on_cooldown() is False


# ---------------------------------------------------------------------------
# AuthProfilePool
# ---------------------------------------------------------------------------

class TestAuthProfilePool:
    def _pool(self, *names_and_weights):
        """Helper: create pool from list of (name, weight) tuples."""
        profiles = [
            AuthProfile(name=n, api_key=f"key-{n}", weight=w)
            for n, w in names_and_weights
        ]
        return AuthProfilePool(profiles)

    def test_next_available_returns_profile(self):
        pool = self._pool(("alpha", 1))
        p = pool.next_available()
        assert p is not None
        assert p.name == "alpha"

    def test_empty_pool_returns_none(self):
        pool = AuthProfilePool([])
        assert pool.next_available() is None

    def test_round_robin_two_profiles(self):
        pool = self._pool(("a", 1), ("b", 1))
        names = [pool.next_available().name for _ in range(4)]
        # Both names should appear
        assert "a" in names
        assert "b" in names

    def test_weighted_profile_appears_more(self):
        pool = self._pool(("heavy", 3), ("light", 1))
        names = [pool.next_available().name for _ in range(40)]
        assert names.count("heavy") > names.count("light")

    def test_mark_failure_puts_profile_on_cooldown(self):
        pool = self._pool(("only", 1))
        pool.mark_failure("only")
        # Now the single profile is on cooldown → returns None
        result = pool.next_available()
        assert result is None

    def test_mark_good_restores_profile(self):
        pool = self._pool(("x", 1))
        pool.mark_failure("x")
        pool.mark_good("x")
        p = pool.next_available()
        assert p is not None
        assert p.name == "x"

    def test_mark_failure_unknown_no_crash(self):
        pool = self._pool(("a", 1))
        pool.mark_failure("nonexistent")  # Should not raise

    def test_all_on_cooldown_returns_none(self):
        pool = self._pool(("p1", 1), ("p2", 1))
        pool.mark_failure("p1")
        pool.mark_failure("p2")
        assert pool.next_available() is None

    def test_add_profile(self):
        pool = self._pool(("a", 1))
        pool.add_profile(AuthProfile(name="b", api_key="kb"))
        assert len(pool) == 2

    def test_remove_profile(self):
        pool = self._pool(("a", 1), ("b", 1))
        pool.remove_profile("a")
        assert len(pool) == 1
        for _ in range(5):
            p = pool.next_available()
            assert p.name == "b"

    def test_len(self):
        pool = self._pool(("a", 1), ("b", 1), ("c", 1))
        assert len(pool) == 3

    def test_healthy_count_all_healthy(self):
        pool = self._pool(("a", 1), ("b", 1))
        assert pool.healthy_count() == 2

    def test_healthy_count_after_failure(self):
        pool = self._pool(("a", 1), ("b", 1))
        pool.mark_failure("a")
        assert pool.healthy_count() == 1

    def test_status_returns_list(self):
        pool = self._pool(("a", 1))
        s = pool.status()
        assert isinstance(s, list)
        assert len(s) == 1
        assert s[0]["name"] == "a"
        assert s[0]["healthy"] is True

    def test_status_reflects_failure(self):
        pool = self._pool(("x", 1))
        pool.mark_failure("x")
        s = pool.status()
        assert s[0]["healthy"] is False
        assert s[0]["failure_count"] == 1
