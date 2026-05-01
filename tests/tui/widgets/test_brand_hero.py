"""Tests for navig/tui/widgets/brand_hero.py."""
from __future__ import annotations
import pytest

class TestBrandHero:
    def _h(self):
        from navig.tui.widgets.brand_hero import BrandHero
        h = BrandHero.__new__(BrandHero); h._content = ""; return h
    def test_initial_empty(self): assert self._h().render() == ""
    def test_set_text(self): h = self._h(); h.set_text("NAVIG"); assert h._content == "NAVIG"
    def test_render_after_set(self): h = self._h(); h.set_text("hi"); assert h.render() == "hi"
    def test_render_str(self): assert isinstance(self._h().render(), str)
