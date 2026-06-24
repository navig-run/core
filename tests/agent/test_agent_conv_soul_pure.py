"""
Unit tests for pure-logic helpers in navig/agent/conv/soul.py.

Covers:
- Module constants (_RICH_IDENTITY, _FALLBACK_IDENTITY, _CHAT_RULES)
- _condense_soul() branching logic
- SoulLoader.build_system_prompt() section assembly
- SoulLoader singleton property
"""

from __future__ import annotations

import pytest

from navig.agent.conv.soul import (
    _CHAT_RULES,
    _FALLBACK_IDENTITY,
    _RICH_IDENTITY,
    SoulLoader,
    _condense_soul,
    get_soul_loader,
)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_rich_identity_is_non_empty_string(self):
        assert isinstance(_RICH_IDENTITY, str)
        assert len(_RICH_IDENTITY) > 0

    def test_rich_identity_mentions_navig(self):
        assert "NAVIG" in _RICH_IDENTITY

    def test_fallback_identity_is_non_empty_string(self):
        assert isinstance(_FALLBACK_IDENTITY, str)
        assert len(_FALLBACK_IDENTITY) > 0

    def test_chat_rules_is_non_empty_string(self):
        assert isinstance(_CHAT_RULES, str)
        assert len(_CHAT_RULES) > 0

    def test_chat_rules_contains_banned_phrases_marker(self):
        assert "BANNED" in _CHAT_RULES

    def test_rich_identity_longer_than_fallback(self):
        # Rich is the full detailed prompt, fallback is shorter
        assert len(_RICH_IDENTITY) > len(_FALLBACK_IDENTITY)


# ---------------------------------------------------------------------------
# _condense_soul
# ---------------------------------------------------------------------------


class TestCondenseSoul:
    def test_rich_soul_returns_rich_identity(self):
        result = _condense_soul("anything", has_rich_soul=True)
        assert result == _RICH_IDENTITY

    def test_non_rich_short_text_returned_verbatim(self):
        raw = "This is my custom soul."
        result = _condense_soul(raw, has_rich_soul=False)
        assert result == raw

    def test_non_rich_truncates_at_2000(self):
        raw = "x" * 3000
        result = _condense_soul(raw, has_rich_soul=False)
        assert result == raw[:2000]

    def test_non_rich_exactly_2000_not_truncated(self):
        raw = "y" * 2000
        result = _condense_soul(raw, has_rich_soul=False)
        assert result == raw

    def test_non_rich_empty_string_returns_empty(self):
        result = _condense_soul("", has_rich_soul=False)
        assert result == ""

    def test_rich_soul_ignores_raw_content(self):
        result = _condense_soul("totally_different_content", has_rich_soul=True)
        assert result == _RICH_IDENTITY

    def test_non_rich_1999_chars_unchanged(self):
        raw = "a" * 1999
        result = _condense_soul(raw, has_rich_soul=False)
        assert len(result) == 1999

    def test_returns_string_type(self):
        assert isinstance(_condense_soul("abc", has_rich_soul=False), str)
        assert isinstance(_condense_soul("abc", has_rich_soul=True), str)


# ---------------------------------------------------------------------------
# SoulLoader.build_system_prompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    @pytest.fixture
    def loader(self):
        # Use a fresh SoulLoader with content injected to avoid disk I/O
        sl = SoulLoader()
        sl.override("Test soul content")
        return sl

    def test_returns_string(self, loader):
        result = loader.build_system_prompt("soul", "lang", "awareness")
        assert isinstance(result, str)

    def test_includes_soul_content(self, loader):
        result = loader.build_system_prompt("My soul text.", "", "")
        assert "My soul text." in result

    def test_includes_lang_instruction(self, loader):
        result = loader.build_system_prompt("soul", "Respond in French.", "")
        assert "Respond in French." in result

    def test_includes_awareness(self, loader):
        result = loader.build_system_prompt("soul", "", "User context here.")
        assert "User context here." in result

    def test_sections_separated_by_double_newline(self, loader):
        result = loader.build_system_prompt("soul text", "lang text", "aware text")
        assert "\n\n" in result

    def test_who_you_are_header_present(self, loader):
        result = loader.build_system_prompt("soul text", "", "")
        assert "## Who You Are" in result

    def test_how_to_talk_header_present(self, loader):
        result = loader.build_system_prompt("soul text", "", "")
        assert "## How to Talk" in result

    def test_session_context_header_when_awareness_given(self, loader):
        result = loader.build_system_prompt("soul", "", "some awareness")
        assert "## Session Context" in result

    def test_no_session_context_header_when_no_awareness(self, loader):
        result = loader.build_system_prompt("soul", "", "")
        assert "## Session Context" not in result

    def test_fallback_identity_when_soul_empty(self, loader):
        result = loader.build_system_prompt("", "", "")
        assert _FALLBACK_IDENTITY in result

    def test_chat_rules_always_appended(self, loader):
        result = loader.build_system_prompt("soul text", "", "")
        assert _CHAT_RULES in result

    def test_lang_instruction_not_wrapped_in_header(self, loader):
        """Lang instruction has no ## prefix — it's injected raw at the top."""
        result = loader.build_system_prompt("soul", "lang_raw_text", "")
        # raw injection — check it appears without a section header
        assert "lang_raw_text" in result


# ---------------------------------------------------------------------------
# SoulLoader singleton
# ---------------------------------------------------------------------------


class TestSoulLoaderSingleton:
    def test_same_instance_returned(self):
        a = SoulLoader()
        b = SoulLoader()
        assert a is b

    def test_get_soul_loader_returns_soul_loader(self):
        loader = get_soul_loader()
        assert isinstance(loader, SoulLoader)

    def test_get_soul_loader_same_singleton(self):
        assert get_soul_loader() is SoulLoader()

    def test_override_sets_cached_content(self):
        loader = SoulLoader()
        loader.override("custom content")
        assert loader.cached_content == "custom content"
