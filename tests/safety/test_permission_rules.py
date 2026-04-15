"""Tests for navig.permissions — rule parsing, matching, and loader."""

from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Rule parsing
# ─────────────────────────────────────────────────────────────────────────────

class TestParseRuleSpec:
    def test_parses_tool_and_pattern(self):
        from navig.permissions.rule_parser import parse_rule_spec
        from navig.permissions.rules import RuleAction

        rule = parse_rule_spec("deny", "Bash(rm -rf /*)", source="test")
        assert rule is not None
        assert rule.tool == "bash"
        assert rule.pattern == "rm -rf /*"
        assert rule.action == RuleAction.DENY

    def test_parses_allow(self):
        from navig.permissions.rule_parser import parse_rule_spec
        from navig.permissions.rules import RuleAction

        rule = parse_rule_spec("allow", "Bash(git commit:*)", source="test")
        assert rule is not None
        assert rule.action == RuleAction.ALLOW

    def test_strips_tool_suffix(self):
        from navig.permissions.rule_parser import parse_rule_spec

        rule = parse_rule_spec("deny", "BashTool(rm *)", source="test")
        assert rule is not None
        assert rule.tool == "bash"

    def test_returns_none_for_invalid_spec(self):
        from navig.permissions.rule_parser import parse_rule_spec

        # Invalid action — not 'allow' or 'deny'
        rule = parse_rule_spec("unknown", "Bash(rm -rf)", source="test")
        assert rule is None


# ─────────────────────────────────────────────────────────────────────────────
# Rule matching
# ─────────────────────────────────────────────────────────────────────────────

class TestPermissionRuleMatch:
    def _make_deny_rule(self, tool: str, pattern: str):
        from navig.permissions.rules import PermissionRule, RuleAction

        return PermissionRule(
            action=RuleAction.DENY,
            tool=tool,
            pattern=pattern,
            source="test",
        )

    def test_matches_exact_tool(self):
        rule = self._make_deny_rule("bash", "rm -rf *")
        assert rule.matches("bash", "rm -rf /tmp") is True

    def test_no_match_different_tool(self):
        rule = self._make_deny_rule("bash", "rm -rf *")
        assert rule.matches("python", "rm -rf /tmp") is False

    def test_wildcard_tool_matches_any(self):
        rule = self._make_deny_rule("*", "DROP TABLE*")
        assert rule.matches("bash", "DROP TABLE users") is True
        assert rule.matches("python", "DROP TABLE users") is True

    def test_glob_pattern_in_input(self):
        rule = self._make_deny_rule("bash", "rm -rf /*")
        # fnmatch: 'rm -rf /*' matches 'rm -rf /var/www'
        assert rule.matches("bash", "rm -rf /var/www") is True

    def test_substring_fallback(self):
        # When fnmatch doesn't match, fall back to substring
        rule = self._make_deny_rule("bash", "DROP TABLE")
        assert rule.matches("bash", "DROP TABLE users;") is True


# ─────────────────────────────────────────────────────────────────────────────
# check_permission integration
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckPermission:
    def test_returns_not_denied_when_no_rules(self, monkeypatch):
        """Without a rules file the system should allow all (fail open)."""
        from navig.permissions import check_permission
        from navig.permissions.loader import PermissionRuleLoader

        loader = PermissionRuleLoader()
        loader._rules = []  # empty rules
        loader._enabled = True
        monkeypatch.setattr("navig.permissions._loader", loader)
        decision = check_permission("bash", "ls -la")
        assert decision.denied is False

    def test_deny_rule_triggers(self, monkeypatch):
        from navig.permissions import check_permission
        from navig.permissions.loader import PermissionRuleLoader
        from navig.permissions.rule_parser import parse_rule_spec

        rule = parse_rule_spec("deny", "Bash(rm -rf *)", source="test")
        loader = PermissionRuleLoader()
        loader._rules = [rule]
        loader._enabled = True
        monkeypatch.setattr("navig.permissions._loader", loader)

        decision = check_permission("bash", "rm -rf /tmp/test")
        assert decision.denied is True
        assert decision.matching_rule is not None

    def test_allow_rule_passes(self, monkeypatch):
        from navig.permissions import check_permission
        from navig.permissions.loader import PermissionRuleLoader
        from navig.permissions.rule_parser import parse_rule_spec

        rule = parse_rule_spec("allow", "Bash(git *)", source="test")
        loader = PermissionRuleLoader()
        loader._rules = [rule]
        loader._enabled = True
        monkeypatch.setattr("navig.permissions._loader", loader)

        decision = check_permission("bash", "git status")
        assert decision.denied is False
        assert decision.matching_rule is not None
