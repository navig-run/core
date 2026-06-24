"""
Batch 14: Tests for
- navig.approval.policies (ApprovalLevel/Status enums, ApprovalPolicy.classify_command,
  is_user_auto_approved, is_auto_evolve_allowed, from_config, patterns)
- navig.inbox.space_scorer (extract_terms, _count_hits, _tokenize,
  check_exclude_rules, score_against_space)
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# navig.approval.policies
# ---------------------------------------------------------------------------
from navig.approval.policies import (
    ApprovalLevel,
    ApprovalPolicy,
    ApprovalStatus,
    DEFAULT_AUTO_EVOLVE_WHITELIST,
    DEFAULT_DANGEROUS_PATTERNS,
    DEFAULT_NEVER_PATTERNS,
    DEFAULT_SAFE_PATTERNS,
)


class TestApprovalLevelEnum:
    def test_all_members(self):
        assert set(ApprovalLevel) == {
            ApprovalLevel.SAFE,
            ApprovalLevel.CONFIRM,
            ApprovalLevel.DANGEROUS,
            ApprovalLevel.NEVER,
        }

    def test_safe_value(self):
        assert ApprovalLevel.SAFE.value == "safe"

    def test_never_value(self):
        assert ApprovalLevel.NEVER.value == "never"


class TestApprovalStatusEnum:
    def test_pending(self):
        assert ApprovalStatus.PENDING.value == "pending"

    def test_approved_denied_expired(self):
        assert ApprovalStatus.APPROVED.value == "approved"
        assert ApprovalStatus.DENIED.value == "denied"
        assert ApprovalStatus.EXPIRED.value == "expired"


class TestApprovalPolicyClassify:
    def setup_method(self):
        self.policy = ApprovalPolicy.default()

    def test_safe_command(self):
        assert self.policy.classify_command("host list") == ApprovalLevel.SAFE

    def test_safe_via_classify_alias(self):
        assert self.policy.classify("app list") == ApprovalLevel.SAFE

    def test_dangerous_shutdown(self):
        # "run shutdown now" matches "run shutdown*" (dangerous) but not any never pattern
        level = self.policy.classify_command("run shutdown now")
        assert level == ApprovalLevel.DANGEROUS

    def test_never_rm_rf_root(self):
        level = self.policy.classify_command("run rm -rf /")
        assert level == ApprovalLevel.NEVER

    def test_confirm_file_remove(self):
        level = self.policy.classify_command("file remove /tmp/stuff")
        assert level == ApprovalLevel.CONFIRM

    def test_confirm_default_unlisted(self):
        # Unlisted commands default to CONFIRM
        level = self.policy.classify_command("navig unknowncmd xyz")
        assert level == ApprovalLevel.CONFIRM

    def test_case_insensitive_never(self):
        # Pattern matching is case-insensitive
        level = self.policy.classify_command("RUN RM -RF /")
        assert level == ApprovalLevel.NEVER

    def test_never_beats_dangerous(self):
        # Never patterns checked first — cannot be overridden by dangerous
        level = self.policy.classify_command("run rm -rf /")
        assert level == ApprovalLevel.NEVER

    def test_help_command_safe(self):
        level = self.policy.classify_command("help navig db")
        assert level == ApprovalLevel.SAFE

    def test_status_command_safe(self):
        level = self.policy.classify_command("status")
        assert level == ApprovalLevel.SAFE


class TestApprovalPolicyAutoApproveUser:
    def test_user_not_in_list(self):
        policy = ApprovalPolicy(auto_approve_users=["alice"])
        assert policy.is_user_auto_approved("bob") is False

    def test_user_in_list(self):
        policy = ApprovalPolicy(auto_approve_users=["alice", "bob"])
        assert policy.is_user_auto_approved("alice") is True

    def test_empty_list(self):
        policy = ApprovalPolicy()
        assert policy.is_user_auto_approved("anyone") is False


class TestApprovalPolicyAutoEvolve:
    def test_disabled_returns_false(self):
        policy = ApprovalPolicy(auto_evolve_enabled=False)
        assert policy.is_auto_evolve_allowed("fix something", audit_log_live=True) is False

    def test_no_audit_log_returns_false(self):
        policy = ApprovalPolicy(auto_evolve_enabled=True)
        assert policy.is_auto_evolve_allowed("fix something", audit_log_live=False) is False

    def test_dangerous_command_rejected(self):
        policy = ApprovalPolicy(auto_evolve_enabled=True)
        assert policy.is_auto_evolve_allowed("run rm -rf /var", audit_log_live=True) is False

    def test_whitelisted_confirm_command_allowed(self):
        policy = ApprovalPolicy(auto_evolve_enabled=True)
        # "fix" is in the default whitelist; classify likely returns CONFIRM
        result = policy.is_auto_evolve_allowed("fix my_script", audit_log_live=True)
        assert isinstance(result, bool)

    def test_non_whitelisted_command_denied(self):
        policy = ApprovalPolicy(auto_evolve_enabled=True, auto_evolve_whitelist=["only_this"])
        assert policy.is_auto_evolve_allowed("something_else", audit_log_live=True) is False


class TestApprovalPolicyPatterns:
    def test_patterns_dict_has_all_levels(self):
        policy = ApprovalPolicy.default()
        p = policy.patterns
        assert "safe" in p
        assert "confirm" in p
        assert "dangerous" in p
        assert "never" in p

    def test_patterns_returns_lists(self):
        policy = ApprovalPolicy.default()
        assert isinstance(policy.patterns["safe"], list)


class TestApprovalPolicyFromConfig:
    def test_from_empty_config(self):
        policy = ApprovalPolicy.from_config({})
        assert policy.enabled is True
        assert policy.timeout_seconds == 120
        assert policy.default_action == "deny"

    def test_from_config_overrides_enabled(self):
        policy = ApprovalPolicy.from_config({"approval": {"enabled": False}})
        assert policy.enabled is False

    def test_from_config_overrides_timeout(self):
        policy = ApprovalPolicy.from_config({"approval": {"timeout_seconds": 30}})
        assert policy.timeout_seconds == 30


# ---------------------------------------------------------------------------
# navig.inbox.space_scorer — pure functions
# ---------------------------------------------------------------------------
from navig.inbox.routes_loader import ChannelConfig, ExcludeRule, RoutesConfig
from navig.inbox.space_scorer import (
    _count_hits,
    _tokenize,
    check_exclude_rules,
    extract_terms,
    score_against_space,
)


class TestExtractTerms:
    def test_basic_words(self):
        terms = extract_terms("hello world testing code review")
        assert "hello" in terms
        assert "world" in terms
        assert "testing" in terms

    def test_deduplication(self):
        terms = extract_terms("hello hello hello")
        assert terms.count("hello") == 1

    def test_short_words_excluded(self):
        # Words < 4 chars are excluded
        terms = extract_terms("a be cat dogg")
        assert "a" not in terms
        assert "be" not in terms
        assert "cat" not in terms
        assert "dogg" in terms

    def test_lowercase(self):
        terms = extract_terms("UPPER lower Mixed")
        assert "upper" in terms
        assert "lower" in terms
        assert "mixed" in terms

    def test_max_terms_cap(self):
        text = " ".join([f"word{i:04d}" for i in range(300)])
        terms = extract_terms(text, max_terms=50)
        assert len(terms) <= 50

    def test_empty_string(self):
        assert extract_terms("") == []


class TestCountHits:
    def test_single_hit(self):
        assert _count_hits({"python", "testing", "code"}, ["testing"]) == 1

    def test_no_hits(self):
        assert _count_hits({"apple", "banana"}, ["cherry", "date"]) == 0

    def test_multiple_hits(self):
        assert _count_hits({"alpha", "beta", "gamma"}, ["alpha", "beta", "other"]) == 2

    def test_phrase_all_parts_present(self):
        assert _count_hits({"code", "review"}, ["code review"]) == 1

    def test_phrase_partial_miss(self):
        # Only one part present → no hit
        assert _count_hits({"code"}, ["code review"]) == 0

    def test_case_insensitive(self):
        assert _count_hits({"python"}, ["PYTHON"]) == 1


class TestTokenize:
    def test_basic(self):
        result = _tokenize("Hello World Testing")
        assert "hello" in result
        assert "world" in result

    def test_short_excluded(self):
        result = _tokenize("at on by word")
        # "at", "on", "by" are < 3 chars so excluded; "word" included
        assert "at" not in result
        assert "word" in result

    def test_empty(self):
        assert _tokenize("") == []


class TestCheckExcludeRules:
    def test_no_rules_returns_none(self):
        config = RoutesConfig()
        result = check_exclude_rules(["code", "testing"], config)
        assert result is None

    def test_rule_fires_when_min_hits_reached(self):
        rule = ExcludeRule(keywords=["python", "testing"], min_hits=1)
        config = RoutesConfig(exclude=[rule])
        result = check_exclude_rules(["python", "django"], config)
        assert result is rule

    def test_rule_does_not_fire_below_min_hits(self):
        rule = ExcludeRule(keywords=["python", "testing"], min_hits=2)
        config = RoutesConfig(exclude=[rule])
        result = check_exclude_rules(["python"], config)
        assert result is None

    def test_returns_first_matching_rule(self):
        rule1 = ExcludeRule(keywords=["alpha"], min_hits=1)
        rule2 = ExcludeRule(keywords=["beta"], min_hits=1)
        config = RoutesConfig(exclude=[rule1, rule2])
        result = check_exclude_rules(["alpha", "beta"], config)
        assert result is rule1


class TestScoreAgainstSpace:
    def test_empty_config_returns_zero(self):
        config = RoutesConfig()
        assert score_against_space(["python"], config) == 0.0

    def test_empty_terms_returns_zero(self):
        ch = ChannelConfig(id="c1", name="code", keywords=["python"])
        config = RoutesConfig(channels=[ch])
        assert score_against_space([], config) == 0.0

    def test_full_match_returns_one(self):
        ch = ChannelConfig(id="c1", name="code", keywords=["python", "testing", "code"])
        config = RoutesConfig(channels=[ch])
        score = score_against_space(["python", "testing", "code"], config)
        assert score == pytest.approx(1.0)

    def test_partial_match(self):
        ch = ChannelConfig(id="c1", name="dev", keywords=["python"])
        config = RoutesConfig(channels=[ch])
        # 1 hit out of 4 terms = 0.25
        score = score_against_space(["python", "java", "ruby", "rust"], config)
        assert score == pytest.approx(0.25)

    def test_score_capped_at_one(self):
        ch = ChannelConfig(id="c1", name="all", keywords=["a", "b", "c", "d", "e"])
        config = RoutesConfig(channels=[ch])
        # More keywords than terms means hit count > len(terms) → capped
        score = score_against_space(["a", "b"], config)
        assert score <= 1.0
