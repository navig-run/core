"""Tests for navig.agent.auth_profiles."""

from __future__ import annotations

import pytest

from navig.agent.auth_profiles import (
    AuthProfile,
    AuthProfilePool,
    ProfileCooldown,
    get_profile_pool,
    reset_profile_pool,
)

# ---------------------------------------------------------------------------
# ProfileCooldown
# ---------------------------------------------------------------------------


class TestProfileCooldown:
    def test_new_cd_not_on_cooldown(self):
        cd = ProfileCooldown()
        assert not cd.is_on_cooldown()
        assert cd.remaining_seconds() == 0.0

    def test_record_failure_exponential(self):
        cd = ProfileCooldown()
        cd.record_failure()  # failure 1 → 5s
        assert cd.cooldown_seconds == pytest.approx(5.0)
        cd.record_failure()  # failure 2 → 10s
        assert cd.cooldown_seconds == pytest.approx(10.0)
        cd.record_failure()  # failure 3 → 20s
        assert cd.cooldown_seconds == pytest.approx(20.0)

    def test_record_failure_capped(self):
        cd = ProfileCooldown()
        for _ in range(20):
            cd.record_failure()
        assert cd.cooldown_seconds == 300.0  # MAX_COOLDOWN

    def test_on_cooldown_true(self):
        cd = ProfileCooldown()
        cd.record_failure()
        # Just recorded; cooldown window starts now — still on cooldown
        assert cd.is_on_cooldown()

    def test_reset_clears_state(self):
        cd = ProfileCooldown()
        cd.record_failure()
        cd.reset()
        assert not cd.is_on_cooldown()
        assert cd.failure_count == 0
        assert cd.cooldown_seconds == 0.0


# ---------------------------------------------------------------------------
# AuthProfilePool — round-robin
# ---------------------------------------------------------------------------


def _pool(*names):
    profiles = [
        AuthProfile(name=n, api_key=f"sk-{n}", provider="openai") for n in names
    ]
    return AuthProfilePool(profiles)


class TestAuthProfilePool:
    def test_next_available_returns_profile(self):
        pool = _pool("a", "b", "c")
        profile = pool.next_available()
        assert profile is not None
        assert profile.name in ("a", "b", "c")

    def test_round_robin_cycles(self):
        pool = _pool("a", "b", "c")
        names = [pool.next_available().name for _ in range(6)]
        # Every profile should appear at least once in 6 picks
        assert set(names) == {"a", "b", "c"}

    def test_skips_on_cooldown(self):
        pool = _pool("a", "b")
        pool.mark_failure("a")
        pool.mark_failure("a")
        # "a" is on cooldown; should only get "b"
        for _ in range(10):
            p = pool.next_available()
            if p is not None:
                assert p.name == "b"

    def test_all_on_cooldown_returns_none(self):
        pool = _pool("x")
        pool.mark_failure("x")
        assert pool.next_available() is None

    def test_mark_good_resets(self):
        pool = _pool("a")
        pool.mark_failure("a")
        assert pool.next_available() is None
        pool.mark_good("a")
        assert pool.next_available() is not None
        assert pool.next_available().name == "a"

    def test_status_snapshot(self):
        pool = _pool("a", "b")
        pool.mark_failure("a")
        status = pool.status()
        assert len(status) == 2
        names = {s["name"]: s for s in status}
        assert names["a"]["healthy"] is False
        assert names["b"]["healthy"] is True

    def test_weight_respected(self):
        profiles = [
            AuthProfile(name="heavy", api_key="sk-h", provider="openai", weight=3),
            AuthProfile(name="light", api_key="sk-l", provider="openai", weight=1),
        ]
        pool = AuthProfilePool(profiles)
        names = [pool.next_available().name for _ in range(40)]
        heavy_count = names.count("heavy")
        light_count = names.count("light")
        # With weight 3:1 we expect ~3× more "heavy" picks
        assert heavy_count > light_count * 2

    def test_add_remove_profile(self):
        pool = _pool("a")
        pool.add_profile(AuthProfile(name="b", api_key="sk-b", provider="openai"))
        assert len(pool) == 2
        pool.remove_profile("a")
        assert len(pool) == 1
        assert pool.next_available().name == "b"

    def test_empty_pool_returns_none(self):
        pool = AuthProfilePool([])
        assert pool.next_available() is None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestGetProfilePool:
    def setup_method(self):
        reset_profile_pool()

    def teardown_method(self):
        reset_profile_pool()

    def test_singleton_is_same_object(self):
        p1 = get_profile_pool()
        p2 = get_profile_pool()
        assert p1 is p2

    def test_returns_pool_instance(self):
        pool = get_profile_pool()
        assert isinstance(pool, AuthProfilePool)
