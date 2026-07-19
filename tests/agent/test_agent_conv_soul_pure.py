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
    _MAX_SOUL_CHARS,
    _RICH_IDENTITY,
    SoulLoader,
    _condense_soul,
    _scan_soul_files,
    get_soul_loader,
    load_soul_content,
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

    def test_chat_rules_guard_against_sycophancy(self):
        # Regression for the "No way" -> "Yeah, totally!" reply: the persona must
        # discourage reflexive agreement / flattery, not just corporate filler.
        low = _CHAT_RULES.lower()
        assert "yes-man" in low
        assert "yeah, totally" in low

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

    def test_full_prompt_guards_against_sycophancy(self, loader):
        # The full conversational prompt carries the anti-yes-man rule.
        result = loader.build_system_prompt("soul text", "", "")
        assert "yes-man" in result.lower()

    def test_minimal_prompt_guards_against_sycophancy(self, loader):
        # The SHORT-chat path (where "No way" -> "Yeah, totally!" came from) is a
        # separate, slimmer prompt — it must ALSO discourage reflexive agreement.
        result = loader.build_minimal_prompt()
        low = result.lower()
        assert "yes-man" in low
        assert "yeah, totally" in low
        # …without losing the intended brevity/warmth or the plain-text rule.
        assert "briefly" in low and "plain text" in low

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

    def test_capabilities_section_when_given(self, loader):
        result = loader.build_system_prompt(
            "soul", "", "", capabilities="- Browse websites\n- Run commands"
        )
        assert "## What You Can Do" in result
        assert "Browse websites" in result
        # It appears BEFORE the chat rules (identity → capabilities → how-to-talk).
        assert result.index("## What You Can Do") < result.index("## How to Talk")

    def test_no_capabilities_section_when_empty(self, loader):
        result = loader.build_system_prompt("soul", "", "", capabilities="")
        assert "## What You Can Do" not in result

    def test_lang_instruction_not_wrapped_in_header(self, loader):
        """Lang instruction has no ## prefix — it's injected raw at the top."""
        result = loader.build_system_prompt("soul", "lang_raw_text", "")
        # raw injection — check it appears without a section header
        assert "lang_raw_text" in result

    def test_minimal_prompt_carries_compact_capabilities(self, loader):
        """The slim path still advertises breadth — language-agnostic, so a short
        non-English 'what can you do?' never improvises a narrow list."""
        result = loader.build_minimal_prompt(
            capabilities="browse & operate websites, run your servers over SSH"
        )
        assert "You have real tools" in result
        assert "browse & operate websites" in result
        # guarded so a plain greeting doesn't dump the list unprompted
        assert "only if asked what you can do" in result

    def test_minimal_prompt_omits_line_without_capabilities(self, loader):
        result = loader.build_minimal_prompt(capabilities="")
        assert "You have real tools" not in result


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


# ---------------------------------------------------------------------------
# Regression: user-authored SOUL.md must win over the hardcoded _RICH_IDENTITY
# constant (the _condense_soul footgun). Before the fix, editing SOUL.md did
# nothing for the chat agent because any rich source returned the constant.
# ---------------------------------------------------------------------------


class TestCondenseSoulUserOverride:
    def test_workspace_source_wins_over_rich_identity(self):
        raw = "I am the Operator's totally custom identity."
        result = _condense_soul(raw, has_rich_soul=True, source="workspace")
        assert result == raw
        assert result != _RICH_IDENTITY

    def test_workspace_honored_even_when_tier_not_rich(self):
        raw = "custom soul"
        assert _condense_soul(raw, has_rich_soul=False, source="workspace") == raw

    def test_workspace_bounded_at_max_chars(self):
        raw = "z" * (_MAX_SOUL_CHARS + 500)
        result = _condense_soul(raw, has_rich_soul=True, source="workspace")
        assert result.startswith("z" * 100)
        assert result.endswith("…")
        assert len(result) <= _MAX_SOUL_CHARS + 2

    def test_workspace_exactly_max_not_truncated(self):
        raw = "q" * _MAX_SOUL_CHARS
        assert _condense_soul(raw, has_rich_soul=True, source="workspace") == raw

    def test_empty_workspace_falls_through_to_rich(self):
        assert _condense_soul("", has_rich_soul=True, source="workspace") == _RICH_IDENTITY

    def test_resources_source_still_uses_rich_identity(self):
        raw = "the full SOUL.default.md doc, too long to inject every turn"
        assert _condense_soul(raw, has_rich_soul=True, source="resources") == _RICH_IDENTITY

    def test_legacy_two_arg_call_unchanged(self):
        # Back-compat: callers passing no source keep the original behavior.
        assert _condense_soul("x", has_rich_soul=True) == _RICH_IDENTITY
        assert _condense_soul("hello", has_rich_soul=False) == "hello"


class TestScanSoulFilesPriority:
    """(raw, has_rich, source) for the highest-priority existing candidate;
    a user SOUL.md outranks the shipped package default."""

    def _patch_candidates(
        self, tmp_path, monkeypatch, *, workspace=None, resources=None, context=None
    ):
        import navig.agent.conv.soul as soulmod

        entries = []
        for name, tag, content in (
            ("workspace_SOUL.md", "workspace", workspace),
            ("resources_SOUL.md", "resources", resources),
            ("context_SOUL.md", "context", context),
        ):
            p = tmp_path / name
            if content is not None:
                p.write_text(content, encoding="utf-8")
            entries.append((p, tag))
        monkeypatch.setattr(soulmod, "_soul_candidates", lambda: entries)

    def test_returns_three_tuple(self, tmp_path, monkeypatch):
        self._patch_candidates(tmp_path, monkeypatch, resources="default identity")
        result = _scan_soul_files()
        assert isinstance(result, tuple) and len(result) == 3

    def test_workspace_outranks_resources(self, tmp_path, monkeypatch):
        self._patch_candidates(
            tmp_path, monkeypatch, workspace="USER SOUL", resources="DEFAULT SOUL"
        )
        raw, has_rich, source = _scan_soul_files()
        assert (raw, has_rich, source) == ("USER SOUL", True, "workspace")

    def test_resources_when_no_workspace(self, tmp_path, monkeypatch):
        self._patch_candidates(tmp_path, monkeypatch, resources="DEFAULT SOUL")
        raw, has_rich, source = _scan_soul_files()
        assert (raw, has_rich, source) == ("DEFAULT SOUL", True, "resources")

    def test_context_only_is_not_rich(self, tmp_path, monkeypatch):
        self._patch_candidates(tmp_path, monkeypatch, context="minimal fallback")
        raw, has_rich, source = _scan_soul_files()
        assert (raw, has_rich, source) == ("minimal fallback", False, "context")

    def test_nothing_found_returns_empty(self, tmp_path, monkeypatch):
        self._patch_candidates(tmp_path, monkeypatch)
        assert _scan_soul_files() == ("", False, "")

    def test_load_soul_content_honors_user_override(self, tmp_path, monkeypatch):
        # End-to-end: a user workspace SOUL.md flows through to the injected identity.
        self._patch_candidates(
            tmp_path,
            monkeypatch,
            workspace="I am the Operator's bespoke voice.",
            resources="ignored default",
        )
        assert load_soul_content() == "I am the Operator's bespoke voice."

    def test_load_soul_content_default_uses_rich(self, tmp_path, monkeypatch):
        self._patch_candidates(tmp_path, monkeypatch, resources="the long default doc")
        assert load_soul_content() == _RICH_IDENTITY

    def test_scan_skips_non_utf8_file_and_degrades(self, tmp_path, monkeypatch):
        # A corrupt-UTF-8 candidate must be skipped (UnicodeDecodeError is a ValueError,
        # not an OSError) and the scan falls through to the next candidate instead of
        # crashing agent construction.
        import navig.agent.conv.soul as soulmod

        bad = tmp_path / "workspace_SOUL.md"
        bad.write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
        good = tmp_path / "resources_SOUL.md"
        good.write_text("good default identity", encoding="utf-8")
        monkeypatch.setattr(soulmod, "_soul_candidates", lambda: [(bad, "workspace"), (good, "resources")])
        raw, has_rich, source = soulmod._scan_soul_files()
        assert raw == "good default identity"
        assert source == "resources"
