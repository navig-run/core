"""
Batch 50 — hermetic unit tests for:
  navig/permissions/rules.py           — RuleAction, PermissionRule.matches()
  navig/permissions/rule_parser.py     — parse_rule_spec, _normalise_tool
  navig/comms/types.py                 — NotificationTarget, DeliveryPriority,
                                         NotificationOptions, DeliveryResult, FanoutResult
  navig/identity/models.py             — SocialLink, UserProfile
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# navig/permissions/rules.py — RuleAction, PermissionRule
# ---------------------------------------------------------------------------

from navig.permissions.rules import PermissionDecision, PermissionRule, RuleAction


class TestRuleAction:
    def test_allow_value(self):
        assert RuleAction.ALLOW.value == "allow"

    def test_deny_value(self):
        assert RuleAction.DENY.value == "deny"

    def test_from_string_allow(self):
        assert RuleAction("allow") == RuleAction.ALLOW

    def test_from_string_deny(self):
        assert RuleAction("deny") == RuleAction.DENY


class TestPermissionRuleMatches:
    def _allow(self, tool: str, pattern: str, source: str = "") -> PermissionRule:
        return PermissionRule(action=RuleAction.ALLOW, tool=tool, pattern=pattern, source=source)

    def test_wildcard_tool_matches_anything(self):
        rule = self._allow("*", "*")
        assert rule.matches("Bash", "git commit") is True

    def test_exact_tool_match(self):
        rule = self._allow("bash", "git*")
        assert rule.matches("bash", "git commit") is True

    def test_case_insensitive_tool_match(self):
        rule = self._allow("bash", "*")
        assert rule.matches("Bash", "anything") is True

    def test_tool_prefix_match_bash_vs_bashtool(self):
        rule = self._allow("bash", "*")
        assert rule.matches("BashTool", "anything") is True

    def test_wrong_tool_no_match(self):
        rule = self._allow("python", "*")
        assert rule.matches("bash", "anything") is False

    def test_glob_pattern_match(self):
        rule = self._allow("bash", "rm -rf /tmp/*")
        assert rule.matches("bash", "rm -rf /tmp/myfile") is True

    def test_glob_pattern_no_match(self):
        rule = self._allow("bash", "rm -rf /tmp/*")
        assert rule.matches("bash", "ls /tmp/") is False

    def test_pattern_wildcard_matches_all(self):
        rule = self._allow("bash", "*")
        assert rule.matches("bash", "any command here") is True

    def test_substring_fallback_match(self):
        rule = self._allow("bash", "git commit")
        assert rule.matches("bash", "git commit -m 'msg'") is True

    def test_deny_rule_action_preserved(self):
        rule = PermissionRule(action=RuleAction.DENY, tool="*", pattern="rm -rf")
        assert rule.action == RuleAction.DENY

    def test_frozen_rule_immutable(self):
        rule = self._allow("bash", "ls*")
        with pytest.raises(Exception):
            rule.pattern = "new_pattern"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# navig/permissions/rule_parser.py — parse_rule_spec, _normalise_tool
# ---------------------------------------------------------------------------

from navig.permissions.rule_parser import _normalise_tool, parse_rule_spec


class TestNormaliseTool:
    def test_bash_unchanged(self):
        assert _normalise_tool("bash") == "bash"

    def test_bashtool_strips_suffix(self):
        assert _normalise_tool("BashTool") == "bash"

    def test_lowercase_applied(self):
        assert _normalise_tool("Python") == "python"

    def test_empty_returns_wildcard(self):
        assert _normalise_tool("") == "*"

    def test_tool_suffix_only_gives_wildcard(self):
        # "tool" alone → strip "tool" → "" → "*"
        assert _normalise_tool("tool") == "*"


class TestParseRuleSpec:
    def test_allow_bash_spec(self):
        rule = parse_rule_spec("allow", "Bash(git commit:*)")
        assert rule is not None
        assert rule.action == RuleAction.ALLOW
        assert rule.tool == "bash"
        assert "git commit" in rule.pattern

    def test_deny_bashtool_spec(self):
        rule = parse_rule_spec("deny", "BashTool(rm -rf /tmp/*)")
        assert rule is not None
        assert rule.action == RuleAction.DENY
        assert rule.tool == "bash"
        assert rule.pattern == "rm -rf /tmp/*"

    def test_wildcard_spec(self):
        rule = parse_rule_spec("allow", "*")
        assert rule is not None
        assert rule.tool == "*"
        assert rule.pattern == "*"

    def test_invalid_action_returns_none(self):
        assert parse_rule_spec("readwrite", "Bash(ls)") is None

    def test_empty_spec_returns_none(self):
        assert parse_rule_spec("allow", "") is None

    def test_none_spec_returns_none(self):
        assert parse_rule_spec("allow", None) is None  # type: ignore[arg-type]

    def test_unknown_action_returns_none(self):
        assert parse_rule_spec("grant", "Bash(ls)") is None

    def test_source_stored(self):
        rule = parse_rule_spec("allow", "Bash(ls)", source="global")
        assert rule.source == "global"

    def test_case_insensitive_action(self):
        rule = parse_rule_spec("ALLOW", "Bash(ls)")
        assert rule is not None
        assert rule.action == RuleAction.ALLOW

    def test_fallback_plain_glob(self):
        # No parens → falls back to tool="*"
        rule = parse_rule_spec("deny", "rm -rf")
        assert rule is not None
        assert rule.tool == "*"
        assert rule.pattern == "rm -rf"


# ---------------------------------------------------------------------------
# navig/comms/types.py — NotificationTarget, DeliveryResult, FanoutResult
# ---------------------------------------------------------------------------

from navig.comms.types import (
    DeliveryPriority,
    DeliveryResult,
    FanoutResult,
    NotificationOptions,
    NotificationTarget,
)


class TestNotificationTarget:
    def test_telegram_factory(self):
        t = NotificationTarget.telegram(chat_id=12345)
        assert t.telegram_chat_id == 12345
        assert t.matrix_room_id is None

    def test_matrix_factory(self):
        t = NotificationTarget.matrix(room_id="!room:server")
        assert t.matrix_room_id == "!room:server"
        assert t.telegram_chat_id is None

    def test_auto_factory(self):
        t = NotificationTarget.auto(user_id="user-001")
        assert t.user_id == "user-001"

    def test_defaults_none(self):
        t = NotificationTarget()
        assert t.telegram_chat_id is None
        assert t.matrix_room_id is None
        assert t.user_id is None

    def test_extra_default_empty(self):
        t = NotificationTarget()
        assert t.extra == {}


class TestDeliveryPriority:
    def test_normal_value(self):
        assert DeliveryPriority.NORMAL.value == "normal"

    def test_critical_value(self):
        assert DeliveryPriority.CRITICAL.value == "critical"

    def test_low_below_normal(self):
        # Just ensure enum members exist
        assert DeliveryPriority.LOW is not DeliveryPriority.NORMAL


class TestNotificationOptions:
    def test_default_priority(self):
        opts = NotificationOptions()
        assert opts.priority == DeliveryPriority.NORMAL

    def test_default_silent_false(self):
        assert NotificationOptions().silent is False

    def test_default_retry_count(self):
        assert NotificationOptions().retry_count == 2

    def test_default_parse_mode(self):
        assert NotificationOptions().parse_mode == "HTML"

    def test_override_values(self):
        opts = NotificationOptions(priority=DeliveryPriority.HIGH, silent=True, retry_count=0)
        assert opts.priority == DeliveryPriority.HIGH
        assert opts.silent is True
        assert opts.retry_count == 0


class TestDeliveryResult:
    def test_success_factory(self):
        r = DeliveryResult.success("telegram", message_id="m123")
        assert r.ok is True
        assert r.channel == "telegram"
        assert r.message_id == "m123"
        assert r.error is None

    def test_failure_factory(self):
        r = DeliveryResult.failure("matrix", error="Connection refused")
        assert r.ok is False
        assert r.error == "Connection refused"

    def test_timestamp_set(self):
        r = DeliveryResult.success("telegram")
        assert r.timestamp is not None

    def test_metadata_default_empty(self):
        r = DeliveryResult.success("telegram")
        assert r.metadata == {}


class TestFanoutResult:
    def test_all_ok_true_when_all_succeed(self):
        r = FanoutResult(results=[
            DeliveryResult.success("telegram"),
            DeliveryResult.success("matrix"),
        ])
        assert r.all_ok is True

    def test_all_ok_false_when_any_fail(self):
        r = FanoutResult(results=[
            DeliveryResult.success("telegram"),
            DeliveryResult.failure("matrix", "err"),
        ])
        assert r.all_ok is False

    def test_any_ok_true_when_one_succeeds(self):
        r = FanoutResult(results=[
            DeliveryResult.failure("telegram", "err"),
            DeliveryResult.success("matrix"),
        ])
        assert r.any_ok is True

    def test_any_ok_false_when_all_fail(self):
        r = FanoutResult(results=[
            DeliveryResult.failure("telegram", "e1"),
            DeliveryResult.failure("matrix", "e2"),
        ])
        assert r.any_ok is False

    def test_empty_results_default(self):
        r = FanoutResult()
        assert r.results == []


# ---------------------------------------------------------------------------
# navig/identity/models.py — SocialLink, UserProfile
# ---------------------------------------------------------------------------

from navig.identity.models import SocialLink, UserProfile


class TestSocialLink:
    def test_basic_construction(self):
        s = SocialLink(platform="github", handle="user123")
        assert s.platform == "github"
        assert s.handle == "user123"

    def test_verified_default_false(self):
        s = SocialLink(platform="twitter", handle="@user")
        assert s.verified is False

    def test_verified_set(self):
        s = SocialLink(platform="discord", handle="user#1234", verified=True)
        assert s.verified is True


class TestUserProfile:
    def _make(self, **kw):
        defaults = dict(telegram_id=12345)
        defaults.update(kw)
        return UserProfile(**defaults)

    def test_telegram_id_stored(self):
        p = self._make()
        assert p.telegram_id == 12345

    def test_username_default_none(self):
        p = self._make()
        assert p.username is None

    def test_language_default_en(self):
        p = self._make()
        assert p.language == "en"

    def test_preferred_channel_default_telegram(self):
        p = self._make()
        assert p.preferred_channel == "telegram"

    def test_socials_default_empty(self):
        p = self._make()
        assert p.socials == []

    def test_to_dict_telegram_id(self):
        p = self._make()
        d = p.to_dict()
        assert d["telegram_id"] == 12345

    def test_to_dict_socials_empty(self):
        p = self._make()
        assert p.to_dict()["socials"] == []

    def test_to_dict_with_social_link(self):
        p = self._make()
        p.socials.append(SocialLink(platform="github", handle="gh-user"))
        d = p.to_dict()
        assert len(d["socials"]) == 1
        assert d["socials"][0]["platform"] == "github"

    def test_to_dict_timestamps_are_iso_strings(self):
        p = self._make()
        d = p.to_dict()
        assert isinstance(d["created_at"], str)
        assert "T" in d["created_at"] or "-" in d["created_at"]

    def test_from_dict_round_trip(self):
        p = self._make(username="myuser", language="fr", telegram_id=99)
        d = p.to_dict()
        restored = UserProfile.from_dict(d)
        assert restored.telegram_id == 99
        assert restored.username == "myuser"
        assert restored.language == "fr"

    def test_from_dict_with_socials(self):
        p = self._make(telegram_id=55)
        p.socials.append(SocialLink(platform="discord", handle="dsc-user"))
        d = p.to_dict()
        restored = UserProfile.from_dict(d)
        assert len(restored.socials) == 1
        assert restored.socials[0].platform == "discord"
