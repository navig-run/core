"""Tests for navig.ui.icons — icon() and nf_icon() resolution."""
from __future__ import annotations

from unittest.mock import patch

import pytest

import navig.ui.icons as icons_mod
from navig.ui.icons import icon, icon_pair, nf_icon


# ── icon() ────────────────────────────────────────────────────


class TestIcon:
    def test_known_icon_safe_mode(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            result = icon("ok")
        assert result == "[ok]"

    def test_known_icon_rich_mode(self):
        with patch.object(icons_mod, "SAFE_MODE", False):
            result = icon("ok")
        assert result == "✓"

    def test_fail_safe_mode(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            assert icon("fail") == "[!!]"

    def test_fail_rich_mode(self):
        with patch.object(icons_mod, "SAFE_MODE", False):
            assert icon("fail") == "✗"

    def test_warn_safe_mode(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            assert icon("warn") == "[!]"

    def test_info_safe_mode(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            assert icon("info") == "[i]"

    def test_arrow_safe_mode(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            assert icon("arrow") == "->"

    def test_bullet_safe_mode(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            assert icon("bullet") == "-"

    def test_unknown_icon_returns_fallback(self):
        with patch.object(icons_mod, "SAFE_MODE", False):
            result = icon("nonexistent_icon_xyz")
        assert result == "?"

    def test_unknown_safe_mode_returns_fallback_safe(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            result = icon("nonexistent_icon_xyz")
        assert result == "?"

    def test_ai_icon_safe_mode(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            assert icon("ai") == "[ai]"

    def test_daemon_icon(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            assert icon("daemon") == "[~]"

    def test_bolt_safe(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            assert icon("bolt") == ">>"

    def test_brain_safe(self):
        with patch.object(icons_mod, "SAFE_MODE", True):
            assert icon("brain") == "[AI]"


# ── icon_pair() ───────────────────────────────────────────────


class TestIconPair:
    def test_returns_tuple(self):
        result = icon_pair("ok")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_rich_first_safe_second(self):
        rich, safe = icon_pair("ok")
        assert rich == "✓"
        assert safe == "[ok]"

    def test_unknown_returns_fallback_tuple(self):
        rich, safe = icon_pair("nonexistent")
        assert rich == "?"
        assert safe == "?"

    def test_not_affected_by_safe_mode(self):
        """icon_pair() always returns both, regardless of SAFE_MODE."""
        with patch.object(icons_mod, "SAFE_MODE", True):
            pair_safe = icon_pair("ok")
        with patch.object(icons_mod, "SAFE_MODE", False):
            pair_rich = icon_pair("ok")
        assert pair_safe == pair_rich


# ── nf_icon() ─────────────────────────────────────────────────


class TestNfIcon:
    def test_falls_back_to_icon_when_no_nerd_font(self):
        with patch.object(icons_mod, "NERD_FONT_AVAILABLE", False):
            with patch.object(icons_mod, "SAFE_MODE", False):
                result = nf_icon("ok")
        # Should be same as icon("ok") in rich mode
        assert result == "✓"

    def test_falls_back_to_safe_icon_when_no_nerd_font_safe_mode(self):
        with patch.object(icons_mod, "NERD_FONT_AVAILABLE", False):
            with patch.object(icons_mod, "SAFE_MODE", True):
                result = nf_icon("ok")
        assert result == "[ok]"

    def test_returns_nf_glyph_when_available(self):
        with patch.object(icons_mod, "NERD_FONT_AVAILABLE", True):
            result = nf_icon("bolt")
        # Should be the nf glyph, not the regular icon
        assert result == "\uf0e7"

    def test_unknown_nf_icon_falls_back_to_icon(self):
        with patch.object(icons_mod, "NERD_FONT_AVAILABLE", True):
            with patch.object(icons_mod, "SAFE_MODE", False):
                result = nf_icon("totally_unknown_xyz")
        assert result == "?"

    def test_nf_brain_glyph(self):
        with patch.object(icons_mod, "NERD_FONT_AVAILABLE", True):
            result = nf_icon("brain")
        assert result == "\U000F18B4"

    def test_nf_git_glyph(self):
        with patch.object(icons_mod, "NERD_FONT_AVAILABLE", True):
            result = nf_icon("git")
        assert result == "\uf1d3"


# ── completeness check ────────────────────────────────────────


class TestRegistryCompleteness:
    def test_all_icons_have_two_strings(self):
        for name, pair in icons_mod._ICONS.items():
            assert isinstance(pair, tuple), f"{name!r} pair is not a tuple"
            assert len(pair) == 2, f"{name!r} pair length != 2"
            assert isinstance(pair[0], str), f"{name!r} rich icon not a string"
            assert isinstance(pair[1], str), f"{name!r} safe icon not a string"

    def test_nf_icons_all_strings(self):
        for name, glyph in icons_mod._NF_ICONS.items():
            assert isinstance(glyph, str), f"NF icon {name!r} not a string"
