"""Regression: `navig config` status icons must reflect a CLI-set boolean.

`navig config set x false` stores the STRING "false", which is truthy — so the raw
`if val` inside `_bool_icon` rendered a *disabled* flag as a green ✓, actively lying about
the setting (the "green light over a broken thing" anti-pattern). `_bool_icon` now routes
through `coerce_bool`, so every `navig config` row is honest.
"""

import pytest

from navig.commands.config import _bool_icon


class TestBoolIconCoercion:
    def test_string_false_renders_cross(self):
        # the footgun: previously "false" (truthy string) → green ✓
        assert "✗" in _bool_icon("false")

    def test_string_true_renders_check(self):
        assert "✓" in _bool_icon("true")

    @pytest.mark.parametrize("val,glyph", [(True, "✓"), (False, "✗"), (None, "✗")])
    def test_real_values(self, val, glyph):
        assert glyph in _bool_icon(val)

    def test_on_off_tokens(self):
        assert "✓" in _bool_icon("on")
        assert "✗" in _bool_icon("off")
