"""Tests for navig.gateway.cooldown — CooldownTracker."""
from __future__ import annotations

import time

import pytest

from navig.gateway.cooldown import CooldownEntry, CooldownTracker


class TestCooldownEntry:
    def test_defaults(self):
        e = CooldownEntry()
        assert e.last_used == 0.0
        assert e.cooldown_s == 60.0
        assert e.call_count == 0
        assert e.deny_count == 0

    def test_custom_values(self):
        e = CooldownEntry(last_used=100.0, cooldown_s=30.0, call_count=5, deny_count=2)
        assert e.call_count == 5
        assert e.deny_count == 2


class TestCooldownTracker:
    def test_first_call_always_allowed(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        allowed, wait = t.check_and_consume("action")
        assert allowed is True
        assert wait == 0.0

    def test_second_call_within_cooldown_denied(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        t.check_and_consume("action")
        allowed, wait = t.check_and_consume("action")
        assert allowed is False
        assert wait > 0

    def test_call_count_increments(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        t.check_and_consume("action")
        stats = t.stats()
        assert stats["action"]["call_count"] == 1

    def test_deny_count_increments(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        t.check_and_consume("action")
        t.check_and_consume("action")
        stats = t.stats()
        assert stats["action"]["deny_count"] == 1

    def test_actor_scoped_keys_separate(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        a1, _ = t.check_and_consume("action", actor="user1")
        a2, _ = t.check_and_consume("action", actor="user2")
        assert a1 is True
        assert a2 is True  # different actor → separate slots

    def test_actor_same_denied_on_second(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        t.check_and_consume("action", actor="user1")
        allowed, wait = t.check_and_consume("action", actor="user1")
        assert allowed is False

    def test_reset_clears_cooldown(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        t.check_and_consume("action")
        t.reset("action")
        allowed, _ = t.check_and_consume("action")
        assert allowed is True

    def test_reset_actor_scoped(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        t.check_and_consume("action", actor="u1")
        t.reset("action", actor="u1")
        allowed, _ = t.check_and_consume("action", actor="u1")
        assert allowed is True

    def test_set_cooldown_updates_existing(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        t.check_and_consume("action")
        t.set_cooldown("action", 300.0)
        stats = t.stats()
        assert stats["action"]["cooldown_s"] == 300.0

    def test_resolve_cooldown_default_action(self):
        t = CooldownTracker(default_cooldown_seconds=30)
        # system.restart has a hardcoded 120s cooldown
        t.check_and_consume("system.restart")
        stats = t.stats()
        assert stats["system.restart"]["cooldown_s"] == 120.0

    def test_resolve_cooldown_unknown_key_uses_default(self):
        t = CooldownTracker(default_cooldown_seconds=45)
        t.check_and_consume("unknown.action")
        stats = t.stats()
        assert stats["unknown.action"]["cooldown_s"] == 45.0

    def test_stats_remaining_zero_when_fresh(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        # Key never seen — stats is empty
        s = t.stats()
        assert s == {}

    def test_stats_keys_present_after_consume(self):
        t = CooldownTracker(default_cooldown_seconds=10)
        t.check_and_consume("k1")
        t.check_and_consume("k2", actor="u1")
        s = t.stats()
        assert "k1" in s
        assert "k2:u1" in s

    def test_remaining_s_positive_during_cooldown(self):
        t = CooldownTracker(default_cooldown_seconds=60)
        t.check_and_consume("x")
        s = t.stats()
        assert s["x"]["remaining_s"] > 0
