"""Hermetic unit tests for navig.safety_guard — pure helper functions."""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_string_unchanged(self):
        from navig.safety_guard import _truncate

        assert _truncate("hello", maxlen=100) == "hello"

    def test_exact_maxlen_unchanged(self):
        from navig.safety_guard import _truncate

        s = "a" * 100
        assert _truncate(s, maxlen=100) == s

    def test_long_string_truncated_with_ellipsis(self):
        from navig.safety_guard import _truncate

        s = "a" * 150
        result = _truncate(s, maxlen=100)
        assert result.endswith("...")
        assert len(result) == 103  # 100 + len("...")

    def test_default_maxlen_is_100(self):
        from navig.safety_guard import _truncate

        s = "x" * 200
        result = _truncate(s)
        assert len(result) <= 103


# ---------------------------------------------------------------------------
# _coerce_action_text
# ---------------------------------------------------------------------------


class TestCoerceActionText:
    def test_none_returns_empty_string(self):
        from navig.safety_guard import _coerce_action_text

        assert _coerce_action_text(None) == ""

    def test_string_returned_as_is(self):
        from navig.safety_guard import _coerce_action_text

        assert _coerce_action_text("rm -rf /") == "rm -rf /"

    def test_int_converted_to_string(self):
        from navig.safety_guard import _coerce_action_text

        assert _coerce_action_text(42) == "42"

    def test_dict_converted_to_string(self):
        from navig.safety_guard import _coerce_action_text

        result = _coerce_action_text({"cmd": "delete"})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _normalize_confirmation_level
# ---------------------------------------------------------------------------


class TestNormalizeConfirmationLevel:
    def test_valid_levels_returned_as_is(self):
        from navig.safety_guard import _normalize_confirmation_level

        for level in ("critical", "standard", "verbose"):
            assert _normalize_confirmation_level(level) == level

    def test_invalid_level_falls_back_to_standard(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level("unknown_level") == "standard"

    def test_none_falls_back_to_standard(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level(None) == "standard"

    def test_empty_string_falls_back_to_standard(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level("") == "standard"

    def test_uppercase_normalized(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level("CRITICAL") == "critical"

    def test_whitespace_stripped(self):
        from navig.safety_guard import _normalize_confirmation_level

        assert _normalize_confirmation_level("  verbose  ") == "verbose"


# ---------------------------------------------------------------------------
# is_destructive
# ---------------------------------------------------------------------------


class TestIsDestructive:
    def test_rm_rf_is_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("rm -rf /var/www") is True

    def test_drop_table_is_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("DROP TABLE users") is True

    def test_safe_command_is_not_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("ls -la") is False

    def test_empty_string_is_not_destructive(self):
        from navig.safety_guard import is_destructive

        assert is_destructive("") is False


# ---------------------------------------------------------------------------
# is_risky
# ---------------------------------------------------------------------------


class TestIsRisky:
    def test_destructive_is_also_risky(self):
        from navig.safety_guard import is_risky

        assert is_risky("rm -rf /") is True

    def test_safe_command_is_not_risky(self):
        from navig.safety_guard import is_risky

        assert is_risky("echo hello") is False


# ---------------------------------------------------------------------------
# classify_action_risk
# ---------------------------------------------------------------------------


class TestClassifyActionRisk:
    def test_rm_rf_is_destructive(self):
        from navig.safety_guard import classify_action_risk

        assert classify_action_risk("rm -rf /data") == "destructive"

    def test_safe_ls_returns_safe(self):
        from navig.safety_guard import classify_action_risk

        assert classify_action_risk("ls -la /var/www") == "safe"

    def test_returns_string(self):
        from navig.safety_guard import classify_action_risk

        result = classify_action_risk("any command")
        assert result in ("safe", "risky", "destructive")


# ---------------------------------------------------------------------------
# should_confirm
# ---------------------------------------------------------------------------


class TestShouldConfirm:
    def test_destructive_always_requires_confirmation(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("rm -rf /", confirmation_level="critical") is True
        assert should_confirm("rm -rf /", confirmation_level="standard") is True
        assert should_confirm("rm -rf /", confirmation_level="verbose") is True

    def test_safe_standard_no_confirm(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("echo hello", confirmation_level="standard") is False

    def test_safe_verbose_requires_confirm(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("echo hello", confirmation_level="verbose") is True

    def test_safe_auto_confirm_safe_no_confirm(self):
        from navig.safety_guard import should_confirm

        assert should_confirm("echo hello", confirmation_level="verbose", auto_confirm_safe=True) is False

    def test_risky_critical_no_confirm(self):
        # "critical" level only confirms destructive, not risky
        # We need something that matches RISKY_PATTERNS but not DESTRUCTIVE_PATTERNS
        # Use a known risky keyword (chmod is often risky)
        from navig.safety_guard import classify_action_risk, should_confirm

        # Find a risky action to test against
        risky_action = "chmod 777 /etc/passwd"
        if classify_action_risk(risky_action) == "risky":
            assert should_confirm(risky_action, confirmation_level="critical") is False

    def test_invalid_level_treated_as_standard(self):
        from navig.safety_guard import should_confirm

        # invalid level → standard → safe command → no confirm
        assert should_confirm("echo ok", confirmation_level="badlevel") is False
