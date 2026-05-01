"""
Batch 127 — tests for navig.bot.contribute_flow pure helpers +
            navig.selfheal.scanner ScanFinding

Coverage targets:
  contribute_flow.py: _MDV2_ESCAPE_CHARS, _escape_md2, _APPROVAL_SEVERITIES,
                      _SEVERITY_EMOJI, _group_by_severity, _format_group_message
  scanner.py:         ScanFinding (fields, validators, confidence clamp, line coerce)
"""

from __future__ import annotations

import pytest

from navig.bot.contribute_flow import (
    _APPROVAL_SEVERITIES,
    _MDV2_ESCAPE_CHARS,
    _SEVERITY_EMOJI,
    _escape_md2,
    _format_group_message,
    _group_by_severity,
)
from navig.selfheal.scanner import ScanFinding


# ===========================================================================
# _escape_md2
# ===========================================================================


class TestEscapeMd2:
    def test_plain_text_unchanged(self):
        assert _escape_md2("hello world") == "hello world"

    def test_dot_escaped(self):
        result = _escape_md2("hello.world")
        assert r"\." in result

    def test_underscore_escaped(self):
        result = _escape_md2("hello_world")
        assert r"\_" in result

    def test_asterisk_escaped(self):
        result = _escape_md2("5 * 3")
        assert r"\*" in result

    def test_parentheses_escaped(self):
        result = _escape_md2("(test)")
        assert r"\(" in result
        assert r"\)" in result

    def test_bracket_escaped(self):
        result = _escape_md2("[link]")
        assert r"\[" in result
        assert r"\]" in result

    def test_backtick_escaped(self):
        result = _escape_md2("`code`")
        assert r"\`" in result

    def test_hyphen_escaped(self):
        result = _escape_md2("item-one")
        assert r"\-" in result

    def test_exclamation_escaped(self):
        result = _escape_md2("Hello!")
        assert r"\!" in result

    def test_empty_string(self):
        assert _escape_md2("") == ""

    def test_already_escaped_not_double_escaped(self):
        # backslash itself should be escaped
        result = _escape_md2("back\\slash")
        assert "\\\\" in result

    def test_returns_string(self):
        assert isinstance(_escape_md2("test"), str)


# ===========================================================================
# Module constants
# ===========================================================================


class TestContributeFlowConstants:
    def test_mdv2_escape_chars_non_empty(self):
        assert len(_MDV2_ESCAPE_CHARS) > 0

    def test_mdv2_escape_chars_contains_dot(self):
        assert "." in _MDV2_ESCAPE_CHARS

    def test_approval_severities_order(self):
        assert list(_APPROVAL_SEVERITIES) == ["critical", "high", "medium"]

    def test_approval_severities_count(self):
        assert len(_APPROVAL_SEVERITIES) == 3

    def test_severity_emoji_has_all_keys(self):
        for sev in _APPROVAL_SEVERITIES:
            assert sev in _SEVERITY_EMOJI

    def test_severity_emoji_are_strings(self):
        for v in _SEVERITY_EMOJI.values():
            assert isinstance(v, str)
            assert len(v) > 0


# ===========================================================================
# _group_by_severity
# ===========================================================================


def _make_finding(severity: str, file: str = "a.py") -> ScanFinding:
    return ScanFinding(
        file=file,
        line=1,
        severity=severity,
        category="security",
        description="desc",
        suggested_fix="fix",
        confidence=0.9,
    )


class TestGroupBySeverity:
    def test_empty_findings_returns_empty(self):
        result = _group_by_severity([])
        assert result == {}

    def test_critical_grouped(self):
        findings = [_make_finding("critical")]
        result = _group_by_severity(findings)
        assert "critical" in result
        assert len(result["critical"]) == 1

    def test_high_grouped(self):
        findings = [_make_finding("high")]
        result = _group_by_severity(findings)
        assert "high" in result

    def test_medium_grouped(self):
        findings = [_make_finding("medium")]
        result = _group_by_severity(findings)
        assert "medium" in result

    def test_low_excluded(self):
        findings = [_make_finding("low")]
        result = _group_by_severity(findings)
        assert "low" not in result
        assert len(result) == 0

    def test_multiple_groups(self):
        findings = [
            _make_finding("critical"),
            _make_finding("high"),
            _make_finding("critical"),
        ]
        result = _group_by_severity(findings)
        assert len(result["critical"]) == 2
        assert len(result["high"]) == 1

    def test_empty_sev_not_in_result(self):
        # Only critical findings — high/medium not in result
        findings = [_make_finding("critical")]
        result = _group_by_severity(findings)
        assert "high" not in result
        assert "medium" not in result


# ===========================================================================
# _format_group_message
# ===========================================================================


class TestFormatGroupMessage:
    def test_returns_string(self):
        findings = [_make_finding("critical")]
        result = _format_group_message("critical", findings)
        assert isinstance(result, str)

    def test_contains_severity_upper(self):
        findings = [_make_finding("high")]
        result = _format_group_message("high", findings)
        assert "HIGH" in result

    def test_contains_file_reference(self):
        findings = [_make_finding("critical", file="navig/foo.py")]
        result = _format_group_message("critical", findings)
        assert "navig" in result

    def test_contains_finding_count(self):
        findings = [_make_finding("medium"), _make_finding("medium")]
        result = _format_group_message("medium", findings)
        assert "2" in result

    def test_contains_emoji(self):
        findings = [_make_finding("critical")]
        result = _format_group_message("critical", findings)
        assert "🔴" in result

    def test_high_emoji(self):
        findings = [_make_finding("high")]
        result = _format_group_message("high", findings)
        assert "🟠" in result

    def test_medium_emoji(self):
        findings = [_make_finding("medium")]
        result = _format_group_message("medium", findings)
        assert "🟡" in result

    def test_unknown_severity_no_crash(self):
        findings = [_make_finding("critical")]
        result = _format_group_message("unknown", findings)
        assert isinstance(result, str)


# ===========================================================================
# ScanFinding
# ===========================================================================


class TestScanFinding:
    def _valid(self, **kwargs) -> ScanFinding:
        defaults = dict(
            file="navig/test.py",
            line=10,
            severity="high",
            category="security",
            description="test issue",
            suggested_fix="fix it",
            confidence=0.85,
        )
        defaults.update(kwargs)
        return ScanFinding(**defaults)

    def test_basic_creation(self):
        f = self._valid()
        assert f.file == "navig/test.py"
        assert f.line == 10
        assert f.severity == "high"
        assert f.confidence == 0.85

    def test_confidence_clamped_above_one(self):
        f = self._valid(confidence=1.5)
        assert f.confidence == 1.0

    def test_confidence_clamped_below_zero(self):
        f = self._valid(confidence=-0.5)
        assert f.confidence == 0.0

    def test_confidence_at_boundary_zero(self):
        f = self._valid(confidence=0.0)
        assert f.confidence == 0.0

    def test_confidence_at_boundary_one(self):
        f = self._valid(confidence=1.0)
        assert f.confidence == 1.0

    def test_confidence_string_coerced(self):
        f = self._valid(confidence="0.75")
        assert f.confidence == 0.75

    def test_confidence_invalid_string_defaults_zero(self):
        f = self._valid(confidence="bad")
        assert f.confidence == 0.0

    def test_line_coerced_from_string(self):
        f = self._valid(line="5")
        assert f.line == 5

    def test_line_minimum_is_one(self):
        f = self._valid(line=0)
        assert f.line == 1

    def test_line_invalid_string_defaults_one(self):
        f = self._valid(line="bad")
        assert f.line == 1

    def test_description_stored(self):
        f = self._valid(description="this is a bug")
        assert f.description == "this is a bug"

    def test_suggested_fix_stored(self):
        f = self._valid(suggested_fix="apply patch")
        assert f.suggested_fix == "apply patch"

    def test_category_stored(self):
        f = self._valid(category="security")
        assert f.category == "security"
