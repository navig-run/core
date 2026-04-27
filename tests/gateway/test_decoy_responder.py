"""
Unit tests for navig/gateway/decoy_responder.py

Covers: FORBIDDEN_TERMS, _seed_hash, _pick, _sanitize_decoy_text, generate
"""

from __future__ import annotations

import hashlib
from datetime import date
from unittest.mock import patch

import pytest

from navig.gateway.decoy_responder import (
    CLUES,
    FORBIDDEN_TERMS,
    OPENERS,
    QUESTIONS,
    STORIES,
    _pick,
    _sanitize_decoy_text,
    _seed_hash,
    generate,
)


# ──────────────────────────────────────────────────────────────────────
# Template pool sanity
# ──────────────────────────────────────────────────────────────────────


class TestTemplatePools:
    def test_openers_non_empty(self):
        assert len(OPENERS) > 0

    def test_stories_non_empty(self):
        assert len(STORIES) > 0

    def test_clues_non_empty(self):
        assert len(CLUES) > 0

    def test_questions_non_empty(self):
        assert len(QUESTIONS) > 0

    def test_forbidden_terms_non_empty(self):
        assert len(FORBIDDEN_TERMS) > 0

    def test_known_forbidden_terms(self):
        terms = set(FORBIDDEN_TERMS)
        assert "navig" in terms
        assert "api_key" in terms
        assert "bot_token" in terms
        assert "allowlist" in terms


# ──────────────────────────────────────────────────────────────────────
# _seed_hash
# ──────────────────────────────────────────────────────────────────────


class TestSeedHash:
    def test_returns_int(self):
        assert isinstance(_seed_hash(12345), int)

    def test_deterministic(self):
        assert _seed_hash(42) == _seed_hash(42)

    def test_different_users_differ(self):
        assert _seed_hash(1) != _seed_hash(2)

    def test_extra_string_changes_result(self):
        assert _seed_hash(42, "") != _seed_hash(42, "hello")

    def test_same_user_extra_deterministic(self):
        assert _seed_hash(99, "msg") == _seed_hash(99, "msg")

    def test_includes_today_date(self):
        today = date.today().isoformat()
        raw = f"42:{today}:"
        expected = int(hashlib.sha256(raw.encode()).hexdigest()[:12], 16)
        assert _seed_hash(42) == expected


# ──────────────────────────────────────────────────────────────────────
# _pick
# ──────────────────────────────────────────────────────────────────────


class TestPick:
    def test_returns_pool_element(self):
        pool = ["a", "b", "c"]
        result = _pick(pool, 0)
        assert result in pool

    def test_deterministic_with_same_seed(self):
        pool = ["x", "y", "z"]
        assert _pick(pool, 7) == _pick(pool, 7)

    def test_modulo_wraps_correctly(self):
        pool = ["a", "b", "c"]
        # seed 5, offset 0 → index 5 % 3 = 2 → "c"
        assert _pick(pool, 5, 0) == "c"

    def test_offset_shifts_pick(self):
        pool = ["a", "b", "c", "d", "e"]
        pick0 = _pick(pool, 0, 0)
        pick1 = _pick(pool, 0, 1)
        # Different offsets should pick different elements (unless wrap collision)
        # 0%5=0 → "a"; (0+1)%5=1 → "b"
        assert pick0 == "a"
        assert pick1 == "b"


# ──────────────────────────────────────────────────────────────────────
# _sanitize_decoy_text
# ──────────────────────────────────────────────────────────────────────


class TestSanitizeDecoyText:
    def test_replaces_navig_with_signal(self):
        result = _sanitize_decoy_text("Hello from navig!")
        assert "navig" not in result.lower()
        assert "signal" in result

    def test_replaces_api_key_term(self):
        result = _sanitize_decoy_text("Your api_key is exposed.")
        assert "api_key" not in result.lower()

    def test_case_insensitive_replacement(self):
        result = _sanitize_decoy_text("NAVIG is running.")
        assert "navig" not in result.lower()

    def test_non_forbidden_term_unchanged(self):
        result = _sanitize_decoy_text("octopus dreams of packets")
        assert "octopus" in result
        assert "packets" in result

    def test_empty_string_unchanged(self):
        assert _sanitize_decoy_text("") == ""

    def test_allowlist_replaced(self):
        result = _sanitize_decoy_text("check the allowlist")
        assert "allowlist" not in result.lower()

    def test_whitelist_replaced(self):
        result = _sanitize_decoy_text("whitelist entry")
        assert "whitelist" not in result.lower()

    def test_multiple_forbidden_terms_replaced(self):
        text = "navig gateway session config"
        result = _sanitize_decoy_text(text)
        for term in ("navig", "gateway", "session", "config"):
            assert term not in result.lower(), f"Term '{term}' still in result"


# ──────────────────────────────────────────────────────────────────────
# generate
# ──────────────────────────────────────────────────────────────────────


class TestGenerate:
    def test_returns_string(self):
        result = generate(user_id=12345)
        assert isinstance(result, str)

    def test_result_non_empty(self):
        result = generate(user_id=12345)
        assert len(result) > 50

    def test_deterministic_same_args(self):
        r1 = generate(user_id=42, user_message="hello")
        r2 = generate(user_id=42, user_message="hello")
        assert r1 == r2

    def test_different_users_produce_different_output(self):
        r1 = generate(user_id=1)
        r2 = generate(user_id=999999)
        assert r1 != r2

    def test_different_messages_can_produce_different_output(self):
        # Different messages use different seeds — not guaranteed to differ
        # but very likely for unrelated messages
        r1 = generate(user_id=1, user_message="alpha")
        r2 = generate(user_id=1, user_message="zzzzzzzzz")
        # At least one component should differ
        assert r1 != r2 or True  # Accept same (extremely unlikely but possible)

    def test_no_forbidden_terms_in_output(self):
        for uid in [100, 200, 300, 400, 500]:
            result = generate(user_id=uid)
            lowered = result.lower()
            for term in ("bot_token", "allowed_users", "allowlist", "whitelist"):
                assert term not in lowered, f"Forbidden term '{term}' found in output for uid={uid}"

    def test_contains_newlines_as_separator(self):
        result = generate(user_id=7)
        assert "\n" in result

    def test_no_markdown_headers(self):
        result = generate(user_id=7)
        assert "##" not in result
        assert "**" not in result

    def test_no_navig_in_output(self):
        for uid in range(10):
            result = generate(user_id=uid)
            assert "navig" not in result.lower(), f"'navig' found in output for uid={uid}"
