"""Tests for navig.permissions.rules — PermissionRule, RuleAction, PermissionDecision."""
from __future__ import annotations

import pytest

from navig.permissions.rules import PermissionDecision, PermissionRule, RuleAction


class TestRuleAction:
    def test_values(self):
        assert RuleAction.ALLOW == "allow"
        assert RuleAction.DENY == "deny"


class TestPermissionRule:
    def _allow(self, tool="bash", pattern="*", **kw):
        return PermissionRule(action=RuleAction.ALLOW, tool=tool, pattern=pattern, **kw)

    def _deny(self, tool="bash", pattern="*", **kw):
        return PermissionRule(action=RuleAction.DENY, tool=tool, pattern=pattern, **kw)

    # --- tool matching ---

    def test_wildcard_tool_matches_any(self):
        rule = self._allow(tool="*")
        assert rule.matches("bash", "ls")
        assert rule.matches("python", "print()")

    def test_exact_tool_match_case_insensitive(self):
        rule = self._allow(tool="bash")
        assert rule.matches("BASH", "ls")
        assert rule.matches("bash", "ls")

    def test_prefix_tool_match(self):
        rule = self._allow(tool="Bash")
        assert rule.matches("BashTool", "ls")

    def test_tool_mismatch_returns_false(self):
        rule = self._allow(tool="python")
        assert rule.matches("bash", "ls") is False

    # --- pattern matching ---

    def test_wildcard_pattern_matches_everything(self):
        rule = self._allow(tool="bash", pattern="*")
        assert rule.matches("bash", "rm -rf /foo")

    def test_empty_pattern_matches_everything(self):
        rule = self._allow(tool="bash", pattern="")
        assert rule.matches("bash", "anything")

    def test_glob_pattern_matching(self):
        rule = self._allow(tool="bash", pattern="rm *")
        assert rule.matches("bash", "rm -rf /tmp")
        assert rule.matches("bash", "rm file.txt")

    def test_glob_pattern_no_match(self):
        rule = self._allow(tool="bash", pattern="rm *")
        assert rule.matches("bash", "ls -la") is False

    def test_substring_fallback_matching(self):
        rule = self._allow(tool="bash", pattern="drop table")
        assert rule.matches("bash", "DROP TABLE users;")  # case-insensitive

    def test_frozen_immutable(self):
        rule = self._allow()
        with pytest.raises((TypeError, AttributeError)):
            rule.action = RuleAction.DENY  # type: ignore[misc]

    def test_description_and_source_optional(self):
        rule = PermissionRule(
            action=RuleAction.ALLOW, tool="*", pattern="*",
            source="global", description="test"
        )
        assert rule.source == "global"
        assert rule.description == "test"


class TestPermissionDecision:
    def test_defaults_not_denied(self):
        d = PermissionDecision()
        assert d.denied is False
        assert d.reason == ""
        assert d.matching_rule is None

    def test_denied_with_reason(self):
        rule = PermissionRule(action=RuleAction.DENY, tool="bash", pattern="rm*")
        d = PermissionDecision(denied=True, reason="rm is blocked", matching_rule=rule)
        assert d.denied is True
        assert "rm" in d.reason
        assert d.matching_rule is rule
