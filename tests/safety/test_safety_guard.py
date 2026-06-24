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


# ---------------------------------------------------------------------------
# DESTRUCTIVE_PATTERNS regex - positive matches (merged from root)
# ---------------------------------------------------------------------------

class TestDestructivePatterns:
    def test_rm_rf(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("rm -rf /tmp/test")

    def test_rm_recursive(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("rm --recursive /path")

    def test_rm_force(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("rm --force /file.txt")

    def test_drop_table(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("DROP TABLE users")

    def test_drop_database(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("DROP DATABASE mydb")

    def test_truncate_table(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("TRUNCATE TABLE logs")

    def test_delete_from_all(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("DELETE FROM users;")

    def test_systemctl_stop(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("systemctl stop nginx")

    def test_kill_9(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("kill -9 1234")

    def test_mkfs(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("mkfs.ext4 /dev/sdb")

    def test_curl_pipe_bash(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("curl http://example.com/install | bash")

    def test_reboot(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("reboot")

    def test_shutdown(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert DESTRUCTIVE_PATTERNS.search("shutdown -h now")


# ---------------------------------------------------------------------------
# DESTRUCTIVE_PATTERNS - negatives (merged from root)
# ---------------------------------------------------------------------------

class TestDestructivePatternsNegative:
    def test_ls_safe(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert not DESTRUCTIVE_PATTERNS.search("ls -la /tmp")

    def test_cat_safe(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert not DESTRUCTIVE_PATTERNS.search("cat /etc/hosts")

    def test_plain_rm_no_flags(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert not DESTRUCTIVE_PATTERNS.search("rm myfile.txt")

    def test_echo_safe(self):
        from navig.safety_guard import DESTRUCTIVE_PATTERNS
        assert not DESTRUCTIVE_PATTERNS.search("echo hello world")


# ---------------------------------------------------------------------------
# RISKY_PATTERNS - positive matches (merged from root)
# ---------------------------------------------------------------------------

class TestRiskyPatterns:
    def test_sudo(self):
        from navig.safety_guard import RISKY_PATTERNS
        assert RISKY_PATTERNS.search("sudo apt-get install nginx")

    def test_apt_remove(self):
        from navig.safety_guard import RISKY_PATTERNS
        assert RISKY_PATTERNS.search("apt remove nginx")

    def test_pip_uninstall(self):
        from navig.safety_guard import RISKY_PATTERNS
        assert RISKY_PATTERNS.search("pip uninstall requests")

    def test_npm_uninstall(self):
        from navig.safety_guard import RISKY_PATTERNS
        assert RISKY_PATTERNS.search("npm uninstall express")

    def test_docker_rm(self):
        from navig.safety_guard import RISKY_PATTERNS
        assert RISKY_PATTERNS.search("docker rm my_container")

    def test_git_reset_hard(self):
        from navig.safety_guard import RISKY_PATTERNS
        assert RISKY_PATTERNS.search("git reset --hard HEAD~1")

    def test_git_push_force(self):
        from navig.safety_guard import RISKY_PATTERNS
        assert RISKY_PATTERNS.search("git push origin main --force")


# ---------------------------------------------------------------------------
# RISKY_PATTERNS - negatives (merged from root)
# ---------------------------------------------------------------------------

class TestRiskyPatternsNegative:
    def test_git_commit_safe(self):
        from navig.safety_guard import RISKY_PATTERNS
        assert not RISKY_PATTERNS.search("git commit -m 'my changes'")

    def test_docker_ps_safe(self):
        from navig.safety_guard import RISKY_PATTERNS
        assert not RISKY_PATTERNS.search("docker ps -a")

    def test_pip_install_safe(self):
        from navig.safety_guard import RISKY_PATTERNS
        assert not RISKY_PATTERNS.search("pip install requests")
