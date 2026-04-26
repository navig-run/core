"""Tests for navig.permissions.rule_parser — parse_rule_spec, _normalise_tool."""
from __future__ import annotations

import pytest

from navig.permissions.rule_parser import _normalise_tool, parse_rule_spec
from navig.permissions.rules import RuleAction


class TestParseRuleSpecAction:
    def test_allow_action(self) -> None:
        rule = parse_rule_spec("allow", "Bash(git commit:*)")
        assert rule is not None
        assert rule.action == RuleAction.ALLOW

    def test_deny_action(self) -> None:
        rule = parse_rule_spec("deny", "Bash(rm -rf /tmp/*)")
        assert rule is not None
        assert rule.action == RuleAction.DENY

    def test_case_insensitive_action(self) -> None:
        rule = parse_rule_spec("ALLOW", "Bash(*)")
        assert rule is not None
        assert rule.action == RuleAction.ALLOW

    def test_invalid_action_returns_none(self) -> None:
        result = parse_rule_spec("grant", "Bash(*)")
        assert result is None

    def test_empty_action_returns_none(self) -> None:
        result = parse_rule_spec("", "Bash(*)")
        assert result is None


class TestParseRuleSpecSpec:
    def test_empty_spec_returns_none(self) -> None:
        result = parse_rule_spec("allow", "")
        assert result is None

    def test_wildcard_spec_matches_all_tools(self) -> None:
        rule = parse_rule_spec("allow", "*")
        assert rule is not None
        assert rule.tool == "*"
        assert rule.pattern == "*"

    def test_tool_with_pattern_parsed(self) -> None:
        rule = parse_rule_spec("allow", "Bash(git commit:*)")
        assert rule is not None
        assert rule.tool == "bash"
        assert "git commit" in rule.pattern

    def test_tool_suffix_stripped(self) -> None:
        rule = parse_rule_spec("deny", "BashTool(rm -rf /tmp/*)")
        assert rule is not None
        assert rule.tool == "bash"

    def test_pattern_preserved(self) -> None:
        rule = parse_rule_spec("allow", "Python(import os:*)")
        assert rule is not None
        assert "import os" in rule.pattern

    def test_fallback_for_unparenthesized_spec(self) -> None:
        rule = parse_rule_spec("allow", "some-pattern-glob")
        assert rule is not None
        assert rule.tool == "*"
        assert rule.pattern == "some-pattern-glob"

    def test_source_stored_in_rule(self) -> None:
        rule = parse_rule_spec("allow", "Bash(*)", source="global")
        assert rule is not None
        assert rule.source == "global"

    def test_source_defaults_to_empty(self) -> None:
        rule = parse_rule_spec("allow", "Bash(*)")
        assert rule is not None
        assert rule.source == ""


class TestNormaliseTool:
    def test_strips_tool_suffix(self) -> None:
        assert _normalise_tool("BashTool") == "bash"

    def test_lowercases(self) -> None:
        assert _normalise_tool("Bash") == "bash"

    def test_no_suffix_just_lowercases(self) -> None:
        assert _normalise_tool("Python") == "python"

    def test_wildcard_preserved(self) -> None:
        assert _normalise_tool("*") == "*"
