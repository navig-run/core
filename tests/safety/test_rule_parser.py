"""Hermetic unit tests for navig.permissions.rule_parser."""
from __future__ import annotations

import pytest

from navig.permissions.rule_parser import _normalise_tool, parse_rule_spec
from navig.permissions.rules import PermissionRule, RuleAction

# ---------------------------------------------------------------------------
# _normalise_tool
# ---------------------------------------------------------------------------


class TestNormaliseTool:
    def test_lowercase(self):
        assert _normalise_tool("Bash") == "bash"

    def test_strips_tool_suffix(self):
        assert _normalise_tool("BashTool") == "bash"

    def test_strips_tool_suffix_lowercase(self):
        assert _normalise_tool("readtool") == "read"

    def test_empty_string_returns_wildcard(self):
        assert _normalise_tool("") == "*"

    def test_wildcard_passthrough(self):
        assert _normalise_tool("*") == "*"

    def test_custom_name_unchanged(self):
        assert _normalise_tool("MyCustomTool") == "mycustom"

    def test_all_caps(self):
        assert _normalise_tool("WRITE") == "write"


# ---------------------------------------------------------------------------
# parse_rule_spec — invalid inputs
# ---------------------------------------------------------------------------


class TestParseRuleSpecInvalid:
    def test_returns_none_for_invalid_action(self):
        assert parse_rule_spec("grant", "Bash(ls)") is None

    def test_returns_none_for_empty_spec(self):
        assert parse_rule_spec("allow", "") is None

    def test_returns_none_for_whitespace_spec(self):
        assert parse_rule_spec("allow", "   ") is None

    def test_returns_none_for_unknown_action_mixed_case(self):
        assert parse_rule_spec("GRANT", "Bash(ls)") is None


# ---------------------------------------------------------------------------
# parse_rule_spec — wildcard
# ---------------------------------------------------------------------------


class TestParseRuleSpecWildcard:
    def test_wildcard_allow(self):
        rule = parse_rule_spec("allow", "*")
        assert rule is not None
        assert rule.action == RuleAction.ALLOW
        assert rule.tool == "*"
        assert rule.pattern == "*"

    def test_wildcard_deny(self):
        rule = parse_rule_spec("deny", "*")
        assert rule is not None
        assert rule.action == RuleAction.DENY

    def test_wildcard_allow_uppercase(self):
        rule = parse_rule_spec("ALLOW", "*")
        assert rule is not None
        assert rule.action == RuleAction.ALLOW


# ---------------------------------------------------------------------------
# parse_rule_spec — tool(pattern) form
# ---------------------------------------------------------------------------


class TestParseRuleSpecToolPattern:
    def test_simple_bash_allow(self):
        rule = parse_rule_spec("allow", "Bash(git commit:*)")
        assert rule is not None
        assert rule.action == RuleAction.ALLOW
        assert rule.tool == "bash"
        assert rule.pattern == "git commit:*"

    def test_bashtool_deny(self):
        rule = parse_rule_spec("deny", "BashTool(rm -rf /tmp/*)")
        assert rule is not None
        assert rule.action == RuleAction.DENY
        assert rule.tool == "bash"
        assert rule.pattern == "rm -rf /tmp/*"

    def test_tool_name_lowercased(self):
        rule = parse_rule_spec("allow", "Read(/etc/passwd)")
        assert rule is not None
        assert rule.tool == "read"

    def test_source_attached(self):
        rule = parse_rule_spec("allow", "Bash(ls)", source="global")
        assert rule is not None
        assert rule.source == "global"

    def test_default_source_empty(self):
        rule = parse_rule_spec("allow", "Bash(ls)")
        assert rule is not None
        assert rule.source == ""

    def test_pattern_with_spaces(self):
        rule = parse_rule_spec("allow", "Bash(echo hello world)")
        assert rule is not None
        assert rule.pattern == "echo hello world"


# ---------------------------------------------------------------------------
# parse_rule_spec — fallback (no parens)
# ---------------------------------------------------------------------------


class TestParseRuleSpecFallback:
    def test_plain_glob_falls_back_to_wildcard_tool(self):
        rule = parse_rule_spec("allow", "git commit:*")
        assert rule is not None
        assert rule.tool == "*"
        assert rule.pattern == "git commit:*"

    def test_plain_string_allow(self):
        rule = parse_rule_spec("allow", "rm -rf")
        assert rule is not None
        assert rule.tool == "*"
        assert rule.action == RuleAction.ALLOW
