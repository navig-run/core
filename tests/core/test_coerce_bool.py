"""Tests for the canonical ``navig.core.coerce.coerce_bool``.

Locks the truth table so the footgun it exists for (``navig config set x false`` →
the string ``"false"`` → ``bool("false") is True``) can never silently regress.
"""

import pytest

from navig.core.coerce import coerce_bool


class TestCoerceBool:
    def test_real_bools_pass_through(self):
        assert coerce_bool(True) is True
        assert coerce_bool(False) is False

    @pytest.mark.parametrize("token", ["1", "true", "TRUE", "True", "yes", "on", " on ", "t", "y"])
    def test_truthy_strings(self, token):
        assert coerce_bool(token) is True

    @pytest.mark.parametrize(
        "token", ["0", "false", "FALSE", "False", "no", "off", " off ", "f", "n", ""]
    )
    def test_falsy_strings(self, token):
        assert coerce_bool(token) is False

    def test_the_footgun(self):
        """The whole reason this module exists: 'false' must be False, not truthy."""
        assert coerce_bool("false") is False
        assert bool("false") is True  # the trap we're guarding against

    def test_none_returns_default(self):
        assert coerce_bool(None) is False
        assert coerce_bool(None, default=True) is True

    def test_unknown_string_returns_default(self):
        """An unrecognised token must NOT silently become truthy."""
        assert coerce_bool("maybe") is False
        assert coerce_bool("maybe", default=True) is True
        assert coerce_bool("enabled-ish", default=False) is False

    def test_numbers(self):
        assert coerce_bool(1) is True
        assert coerce_bool(0) is False
        assert coerce_bool(2) is True
        assert coerce_bool(0.0) is False
        assert coerce_bool(3.14) is True

    def test_case_and_whitespace_insensitive(self):
        assert coerce_bool("  YeS  ") is True
        assert coerce_bool("\tOff\n") is False

    def test_always_returns_a_real_bool(self):
        """Never leaks the raw value — callers rely on an actual bool."""
        for v in (True, False, None, "true", "false", "junk", 0, 1, 5):
            assert isinstance(coerce_bool(v), bool)

    def test_matches_config_set_string_storage_roundtrip(self):
        """The exact scenario: a value the CLI stored as a raw string reads correctly."""
        # navig config set telegram.auto_pin_briefings false  → stored as "false"
        assert coerce_bool("false", default=True) is False
        # navig config set sms.verify_signature true          → stored as "true"
        assert coerce_bool("true", default=False) is True
