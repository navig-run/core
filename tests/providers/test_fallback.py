"""
Tests for navig.providers.fallback — FallbackCandidate, FallbackResult,
CooldownEntry, and FallbackManager cooldown logic.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.providers.fallback import (
    CooldownEntry,
    FallbackCandidate,
    FallbackManager,
    FallbackResult,
)

# ─── FallbackCandidate ────────────────────────────────────────────────────────


def test_fallback_candidate_defaults():
    c = FallbackCandidate(provider_name="openai", model="gpt-4o")
    assert c.provider_name == "openai"
    assert c.model == "gpt-4o"
    assert c.priority == 0
    assert c.config is None


def test_fallback_candidate_with_priority():
    c = FallbackCandidate(provider_name="anthropic", model="claude-3-opus", priority=5)
    assert c.priority == 5


def test_fallback_candidate_fields_stored():
    cfg = MagicMock()
    c = FallbackCandidate(provider_name="groq", model="llama3", priority=2, config=cfg)
    assert c.config is cfg


# ─── FallbackResult ───────────────────────────────────────────────────────────


def test_fallback_result_fields():
    response = MagicMock()
    r = FallbackResult(
        response=response,
        provider_used="openai",
        model_used="gpt-4o-mini",
        attempts=2,
        candidates_tried=["openai:gpt-4o", "anthropic:claude-haiku"],
    )
    assert r.provider_used == "openai"
    assert r.model_used == "gpt-4o-mini"
    assert r.attempts == 2
    assert len(r.candidates_tried) == 2
    assert r.response is response


# ─── CooldownEntry ────────────────────────────────────────────────────────────


def test_cooldown_entry_defaults():
    entry = CooldownEntry(cooldown_until=1000.0)
    assert entry.cooldown_until == 1000.0
    assert entry.failure_count == 1


def test_cooldown_entry_custom_failure_count():
    entry = CooldownEntry(cooldown_until=5000.0, failure_count=3)
    assert entry.failure_count == 3


# ─── FallbackManager constants ────────────────────────────────────────────────


def test_fallback_manager_cooldown_durations_sorted():
    durations = FallbackManager.COOLDOWN_DURATIONS
    assert durations == sorted(durations), "Durations should be in ascending order"


def test_fallback_manager_cooldown_durations_count():
    assert len(FallbackManager.COOLDOWN_DURATIONS) == 4


def test_fallback_manager_cooldown_first_is_5_minutes():
    assert FallbackManager.COOLDOWN_DURATIONS[0] == 5 * 60


def test_fallback_manager_max_cooldown_is_24_hours():
    assert FallbackManager.MAX_COOLDOWN == 24 * 60 * 60


# ─── FallbackManager — init ───────────────────────────────────────────────────


def _make_manager() -> FallbackManager:
    with patch("navig.providers.fallback.AuthProfileManager"), \
         patch("navig.providers.fallback.BUILTIN_PROVIDERS", {}):
        return FallbackManager()


def test_fallback_manager_init_empty_cooldowns():
    mgr = _make_manager()
    assert mgr._cooldowns == {}


# ─── FallbackManager — cooldown key ──────────────────────────────────────────


def test_get_cooldown_key_without_model():
    mgr = _make_manager()
    assert mgr._get_cooldown_key("openai") == "openai"


def test_get_cooldown_key_with_model():
    mgr = _make_manager()
    assert mgr._get_cooldown_key("openai", "gpt-4o") == "openai:gpt-4o"


# ─── FallbackManager — is_in_cooldown ────────────────────────────────────────


def test_is_in_cooldown_no_entry():
    mgr = _make_manager()
    assert mgr.is_in_cooldown("openai") is False


def test_is_in_cooldown_active():
    mgr = _make_manager()
    with patch("navig.providers.fallback.time") as mock_time:
        mock_time.time.return_value = 1000.0
        mgr._cooldowns["openai"] = CooldownEntry(cooldown_until=2000.0, failure_count=1)
        assert mgr.is_in_cooldown("openai") is True


def test_is_in_cooldown_expired():
    mgr = _make_manager()
    with patch("navig.providers.fallback.time") as mock_time:
        mock_time.time.return_value = 3000.0
        mgr._cooldowns["openai"] = CooldownEntry(cooldown_until=2000.0, failure_count=1)
        assert mgr.is_in_cooldown("openai") is False
        # Entry should be removed
        assert "openai" not in mgr._cooldowns


# ─── FallbackManager — get_cooldown_remaining ────────────────────────────────


def test_get_cooldown_remaining_no_entry():
    mgr = _make_manager()
    assert mgr.get_cooldown_remaining("openai") == 0


def test_get_cooldown_remaining_active():
    mgr = _make_manager()
    with patch("navig.providers.fallback.time") as mock_time:
        mock_time.time.return_value = 1000.0
        mgr._cooldowns["openai"] = CooldownEntry(cooldown_until=1300.0, failure_count=1)
        remaining = mgr.get_cooldown_remaining("openai")
        assert remaining == pytest.approx(300.0)


def test_get_cooldown_remaining_never_negative():
    mgr = _make_manager()
    with patch("navig.providers.fallback.time") as mock_time:
        mock_time.time.return_value = 5000.0
        mgr._cooldowns["openai"] = CooldownEntry(cooldown_until=3000.0, failure_count=1)
        assert mgr.get_cooldown_remaining("openai") == 0


# ─── FallbackManager — mark_failure ──────────────────────────────────────────


def test_mark_failure_first_failure_uses_first_duration():
    mgr = _make_manager()
    with patch("navig.providers.fallback.time") as mock_time:
        mock_time.time.return_value = 0.0
        mgr.mark_failure("openai")
        entry = mgr._cooldowns["openai"]
        assert entry.failure_count == 1
        assert entry.cooldown_until == FallbackManager.COOLDOWN_DURATIONS[0]


def test_mark_failure_second_failure_uses_second_duration():
    mgr = _make_manager()
    with patch("navig.providers.fallback.time") as mock_time:
        mock_time.time.return_value = 0.0
        mgr._cooldowns["openai"] = CooldownEntry(cooldown_until=-1.0, failure_count=1)
        mgr.mark_failure("openai")
        entry = mgr._cooldowns["openai"]
        assert entry.failure_count == 2
        assert entry.cooldown_until == FallbackManager.COOLDOWN_DURATIONS[1]


def test_mark_failure_billing_error_uses_max_cooldown():
    from navig.providers.clients import ProviderError

    mgr = _make_manager()
    error = ProviderError("billing error", error_type="billing")
    with patch("navig.providers.fallback.time") as mock_time:
        mock_time.time.return_value = 0.0
        mgr.mark_failure("openai", error=error)
        entry = mgr._cooldowns["openai"]
        assert entry.cooldown_until == FallbackManager.MAX_COOLDOWN


def test_mark_failure_rate_limit_doubles_duration():
    from navig.providers.clients import ProviderError

    mgr = _make_manager()
    error = ProviderError("rate limit", error_type="rate_limit")
    with patch("navig.providers.fallback.time") as mock_time:
        mock_time.time.return_value = 0.0
        mgr.mark_failure("openai", error=error)
        entry = mgr._cooldowns["openai"]
        expected = min(FallbackManager.COOLDOWN_DURATIONS[0] * 2, FallbackManager.MAX_COOLDOWN)
        assert entry.cooldown_until == expected


# ─── FallbackManager — mark_success ──────────────────────────────────────────


def test_mark_success_clears_cooldown():
    mgr = _make_manager()
    mgr._cooldowns["openai"] = CooldownEntry(cooldown_until=9999.0, failure_count=3)
    mgr.mark_success("openai")
    assert "openai" not in mgr._cooldowns


def test_mark_success_no_entry_is_noop():
    mgr = _make_manager()
    # Should not raise even if provider not in cooldowns
    mgr.mark_success("openai")


def test_mark_success_with_model():
    mgr = _make_manager()
    mgr._cooldowns["openai:gpt-4o"] = CooldownEntry(cooldown_until=5000.0)
    mgr.mark_success("openai", "gpt-4o")
    assert "openai:gpt-4o" not in mgr._cooldowns
