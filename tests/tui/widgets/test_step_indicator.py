"""Tests for navig/tui/widgets/step_indicator.py."""
from __future__ import annotations
import pytest

class TestStepIndicator:
    def _ind(self, c=1, t=3, l=None):
        from navig.tui.widgets.step_indicator import StepIndicator
        i = StepIndicator.__new__(StepIndicator); i.current_step = c; i.total_steps = t
        i.step_labels = l or []; return i
    def test_render_str(self): assert isinstance(self._ind().render(), str)
    def test_render_1_of_3(self): r = self._ind(1,3).render(); assert isinstance(r, str)
    def test_render_last(self): r = self._ind(3,3).render(); assert isinstance(r, str)
    def test_render_with_labels(self): r = self._ind(1,2,["A","B"]).render(); assert r is not None
