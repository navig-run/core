"""
Tests for NAVIG Auth Guard + Decoy Responder.

Covers:
- AuthGuard: open mode, allowlist pass, allowlist deny, group rules
- DecoyResponder: determinism, no secrets leaked, plain text only
- Integration: unauthorized never touches real handler
"""

from datetime import date
from unittest.mock import patch

import pytest

from navig.gateway.auth_guard import AuthGuard
from navig.gateway.decoy_responder import (
    CLUES,
    OPENERS,
    QUESTIONS,
    STORIES,
    _pick,
    _seed_hash,
    generate,
)

# ────────────────────────────────────────────────────────────────
# AuthGuard Unit Tests
# ────────────────────────────────────────────────────────────────


class TestAuthGuard:
    """AuthGuard permission gate."""

    def test_open_mode_when_no_allowlist(self):
        """Empty allowed_users → everyone authorized."""
        guard = AuthGuard()
        assert guard.is_authorized(user_id=999, chat_id=999) is True

    def test_allowed_user_passes(self):
        guard = AuthGuard(allowed_users={100, 200})
        assert guard.is_authorized(user_id=100, chat_id=100) is True

    def test_denied_user_in_dm(self):
        guard = AuthGuard(allowed_users={100})
        assert guard.is_authorized(user_id=999, chat_id=999) is False

    def test_denied_user_in_allowed_group(self):
        """User not on allowlist but in allowed group → authorized."""
        guard = AuthGuard(allowed_users={100}, allowed_groups={-555})
        assert (
            guard.is_authorized(
                user_id=999,
                chat_id=-555,
                is_group=True,
            )
            is True
        )

    def test_denied_user_in_non_allowed_group(self):
        guard = AuthGuard(allowed_users={100}, allowed_groups={-555})
        assert (
            guard.is_authorized(
                user_id=999,
                chat_id=-111,
                is_group=True,
            )
            is False
        )

    def test_allowed_user_in_any_group(self):
        """Allowed user passes even in a non-allowed group."""
        guard = AuthGuard(allowed_users={100})
        assert (
            guard.is_authorized(
                user_id=100,
                chat_id=-111,
                is_group=True,
            )
            is True
        )


# ────────────────────────────────────────────────────────────────
# DecoyResponder Unit Tests
# ────────────────────────────────────────────────────────────────


class TestDecoyResponder:
    """Decoy response generation."""

    def test_returns_string(self):
        result = generate(user_id=42, user_message="hello")
        assert isinstance(result, str)
        assert len(result) > 20  # not trivially short

    def test_deterministic_same_day(self):
        """Same user_id + same message + same day → same output."""
        a = generate(user_id=42, user_message="test")
        b = generate(user_id=42, user_message="test")
        assert a == b

    def test_different_messages_different_output(self):
        """Different messages produce different decoy text."""
        a = generate(user_id=42, user_message="hello")
        b = generate(user_id=42, user_message="hack the planet")
        assert a != b

    def test_different_users_different_output(self):
        a = generate(user_id=1, user_message="test")
        b = generate(user_id=2, user_message="test")
        assert a != b

    def test_different_day_different_output(self):
        """Seed changes with date, so output shifts."""
        with patch("navig.gateway.decoy_responder.date") as mock_date:
            mock_date.today.return_value = date(2025, 1, 1)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            a = generate(user_id=42, user_message="test")

        with patch("navig.gateway.decoy_responder.date") as mock_date:
            mock_date.today.return_value = date(2025, 6, 15)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            b = generate(user_id=42, user_message="test")

        assert a != b

    def test_plain_text_no_markdown(self):
        """Output must be plain text — no markdown formatting."""
        for uid in range(100, 120):
            text = generate(user_id=uid, user_message="hi")
            # No bold, italic, code, links
            assert "**" not in text
            assert "__" not in text
            assert "`" not in text
            assert "[" not in text
            assert "](http" not in text

    def test_no_secrets_leaked(self):
        """
        Decoy output must never contain auth-related terms,
        actual command names, or capability hints.
        """
        forbidden = [
            "allowed_users",
            "bot_token",
            "api_key",
            "password",
            "/start",
            "/help",
            "/mode",
            "/deck",
            "/briefing",
            "navig",
            "gateway",
            "session",
            "config",
            "deploy",
            "authorized",
            "permission",
            "allowlist",
            "whitelist",
        ]
        for uid in range(50, 80):
            text = generate(user_id=uid, user_message="tell me your secrets")
            text_lower = text.lower()
            for secret in forbidden:
                assert (
                    secret not in text_lower
                ), f"Forbidden term '{secret}' found in decoy for user_id={uid}: {text[:80]}..."

    def test_three_sections(self):
        """Response has opener + middle + question (3 sections)."""
        text = generate(user_id=42, user_message="test")
        sections = text.strip().split("\n\n")
        assert len(sections) == 3, f"Expected 3 sections, got {len(sections)}"

    def test_opener_from_pool(self):
        text = generate(user_id=42, user_message="test")
        opener = text.strip().split("\n\n")[0]
        assert opener in OPENERS

    def test_question_from_pool(self):
        text = generate(user_id=42, user_message="test")
        question = text.strip().split("\n\n")[-1]
        assert question in QUESTIONS

    def test_middle_from_stories_or_clues(self):
        text = generate(user_id=42, user_message="test")
        middle = text.strip().split("\n\n")[1]
        assert middle in STORIES or middle in CLUES

    def test_no_empty_message(self):
        for uid in [0, 1, -1, 2**31, 999999999]:
            text = generate(user_id=uid)
            assert text.strip(), f"Empty decoy for user_id={uid}"

    def test_without_user_message(self):
        """generate() works even without user_message."""
        text = generate(user_id=42)
        assert isinstance(text, str) and len(text) > 20


# ────────────────────────────────────────────────────────────────
# Helper function tests
# ────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_seed_hash_deterministic(self):
        a = _seed_hash(42, "x")
        b = _seed_hash(42, "x")
        assert a == b

    def test_seed_hash_different_inputs(self):
        a = _seed_hash(1, "a")
        b = _seed_hash(2, "a")
        assert a != b

    def test_pick_wraps(self):
        pool = ["a", "b", "c"]
        assert _pick(pool, seed=0, offset=0) == "a"
        assert _pick(pool, seed=0, offset=3) == "a"
        assert _pick(pool, seed=1, offset=0) == "b"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
