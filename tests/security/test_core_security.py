"""
Batch 32 — navig/core/security.py

Covers:
  - redact_sensitive_text(): mode=off, default patterns, provider-specific prefixes,
    auth headers, connection strings, JSON fields, CLI flags
  - redact_dict(): recursive key-name-based redaction, passthrough of plain values
  - _mask_token(): short vs long masking
  - MissingEnvVarError: attributes and message
  - substitute_env_vars(): string, dict, list, escaped refs, strict=False, missing strict
  - is_safe_executable(): safe list membership, .exe stripping
  - validate_command_safety(): allow_unsafe, dangerous patterns, empty command, unknown exe
  - SecurityFinding.to_dict(): all fields, optional remediation
"""

from __future__ import annotations

import pytest

from navig.core.security import (
    MissingEnvVarError,
    SecurityFinding,
    _mask_token,
    is_safe_executable,
    redact_dict,
    redact_sensitive_text,
    substitute_env_vars,
    validate_command_safety,
)


# ---------------------------------------------------------------------------
# redact_sensitive_text
# ---------------------------------------------------------------------------


class TestRedactSensitiveText:
    def test_empty_string_passthrough(self):
        assert redact_sensitive_text("") == ""

    def test_mode_off_returns_unchanged(self):
        secret = "TOKEN=supersecret123"
        assert redact_sensitive_text(secret, mode="off") == secret

    def test_none_like_empty_passthrough(self):
        # mode off is the only no-op; empty string is caught by `not text`
        assert redact_sensitive_text("", mode="tools") == ""

    def test_env_assignment_redacted(self):
        result = redact_sensitive_text("API_KEY=abc123xyz")
        assert "abc123xyz" not in result
        assert "REDACTED" in result

    def test_token_env_redacted(self):
        result = redact_sensitive_text("TOKEN=mytoken99")
        assert "mytoken99" not in result

    def test_json_api_key_field_redacted(self):
        payload = '{"api_key": "supersecretval"}'
        result = redact_sensitive_text(payload)
        assert "supersecretval" not in result
        assert "REDACTED" in result

    def test_cli_flag_token_redacted(self):
        result = redact_sensitive_text("navig run --token ABCDEF123456")
        assert "ABCDEF123456" not in result
        assert "REDACTED" in result

    def test_bearer_header_redacted(self):
        result = redact_sensitive_text("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "REDACTED" in result

    def test_basic_auth_header_redacted(self):
        result = redact_sensitive_text("Authorization: Basic dXNlcjpwYXNzd29yZA==")
        assert "dXNlcjpwYXNzd29yZA==" not in result

    def test_openai_key_redacted(self):
        result = redact_sensitive_text("my key is sk-abcdefghijklmnopqrstuvwxyz")
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in result

    def test_anthropic_key_redacted(self):
        result = redact_sensitive_text("sk-ant-api03-abcdefghijklmnop")
        assert "abcdefghijklmnop" not in result

    def test_github_pat_redacted(self):
        result = redact_sensitive_text("ghp_abcdefghij1234567890")
        assert "ghp_abcdefghij1234567890" not in result

    def test_slack_token_redacted(self):
        result = redact_sensitive_text("xoxb-1234567890-abcdefghij")
        assert "xoxb-1234567890-abcdefghij" not in result

    def test_mysql_conn_string_redacted(self):
        result = redact_sensitive_text("mysql://user:s3cr3tpass@localhost/db")
        assert "s3cr3tpass" not in result

    def test_postgres_conn_string_redacted(self):
        result = redact_sensitive_text("postgres://admin:mypwd@host/db")
        assert "mypwd" not in result

    def test_plain_text_untouched(self):
        plain = "Hello world, this has no secrets."
        assert redact_sensitive_text(plain) == plain

    def test_custom_pattern_list(self):
        import re
        patterns = [(re.compile(r"CUSTOM_VAL"), "***")]
        result = redact_sensitive_text("value=CUSTOM_VAL", patterns=patterns)
        assert "CUSTOM_VAL" not in result

    def test_aws_access_key_redacted(self):
        result = redact_sensitive_text("key: AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in result


# ---------------------------------------------------------------------------
# redact_dict
# ---------------------------------------------------------------------------


class TestRedactDict:
    def test_password_key_redacted(self):
        result = redact_dict({"password": "secret123"})
        assert result["password"] == "***REDACTED***"

    def test_token_key_redacted(self):
        result = redact_dict({"token": "tok_abc"})
        assert result["token"] == "***REDACTED***"

    def test_plain_key_passthrough(self):
        result = redact_dict({"username": "alice"})
        assert result["username"] == "alice"

    def test_nested_dict_redaction(self):
        result = redact_dict({"db": {"password": "dbpass", "host": "localhost"}})
        assert result["db"]["password"] == "***REDACTED***"
        assert result["db"]["host"] == "localhost"

    def test_list_value_passthrough(self):
        result = redact_dict({"tags": ["a", "b"]})
        assert result["tags"] == ["a", "b"]

    def test_numeric_value_passthrough(self):
        result = redact_dict({"port": 5432})
        assert result["port"] == 5432

    def test_custom_sensitive_key(self):
        result = redact_dict({"my_special": "val"}, sensitive_keys=["my_special"])
        assert result["my_special"] == "***REDACTED***"

    def test_api_key_partial_match_redacted(self):
        # "api_key" contains "key" substring
        result = redact_dict({"api_key": "longvaluehidden"})
        assert result["api_key"] == "***REDACTED***"

    def test_empty_dict(self):
        assert redact_dict({}) == {}


# ---------------------------------------------------------------------------
# _mask_token
# ---------------------------------------------------------------------------


class TestMaskToken:
    def test_short_token_fully_masked(self):
        assert _mask_token("abc") == "***"

    def test_short_boundary_masked(self):
        # 17 chars — still short
        assert _mask_token("a" * 17) == "***"

    def test_long_token_shows_prefix_suffix(self):
        token = "sk-abcDEFGHIJKLMNOPQRST"
        result = _mask_token(token)
        assert result.startswith("sk-abc")
        assert result.endswith(token[-4:])
        assert "..." in result

    def test_exactly_18_chars_is_masked_with_prefix(self):
        token = "a" * 18
        result = _mask_token(token)
        assert "..." in result
        assert result.startswith("aaaaaa")


# ---------------------------------------------------------------------------
# MissingEnvVarError
# ---------------------------------------------------------------------------


class TestMissingEnvVarError:
    def test_attributes(self):
        err = MissingEnvVarError("MY_VAR", "config.db.host")
        assert err.var_name == "MY_VAR"
        assert err.config_path == "config.db.host"

    def test_message_contains_var_name(self):
        err = MissingEnvVarError("API_TOKEN", "services.api.key")
        assert "API_TOKEN" in str(err)

    def test_is_exception(self):
        with pytest.raises(MissingEnvVarError):
            raise MissingEnvVarError("X", "y.z")


# ---------------------------------------------------------------------------
# substitute_env_vars
# ---------------------------------------------------------------------------


class TestSubstituteEnvVars:
    def test_simple_substitution(self):
        result = substitute_env_vars("${MY_VAR}", env={"MY_VAR": "hello"})
        assert result == "hello"

    def test_embedded_substitution(self):
        result = substitute_env_vars("prefix_${X}_suffix", env={"X": "middle"})
        assert result == "prefix_middle_suffix"

    def test_escaped_reference_literal(self):
        result = substitute_env_vars("$${MY_VAR}", env={"MY_VAR": "ignored"})
        assert result == "${MY_VAR}"

    def test_missing_strict_raises(self):
        with pytest.raises(MissingEnvVarError):
            substitute_env_vars("${MISSING_VAR}", env={}, strict=True)

    def test_missing_strict_false_keeps_placeholder(self):
        result = substitute_env_vars("${MISSING_VAR}", env={}, strict=False)
        assert result == "${MISSING_VAR}"

    def test_dict_substitution(self):
        result = substitute_env_vars({"host": "${DB_HOST}"}, env={"DB_HOST": "localhost"})
        assert result["host"] == "localhost"

    def test_list_substitution(self):
        result = substitute_env_vars(["${A}", "${B}"], env={"A": "1", "B": "2"})
        assert result == ["1", "2"]

    def test_scalar_passthrough(self):
        assert substitute_env_vars(42) == 42
        assert substitute_env_vars(None) is None

    def test_no_dollar_sign_passthrough(self):
        assert substitute_env_vars("plain string", env={}) == "plain string"


# ---------------------------------------------------------------------------
# is_safe_executable
# ---------------------------------------------------------------------------


class TestIsSafeExecutable:
    def test_known_safe_ls(self):
        assert is_safe_executable("ls") is True

    def test_known_safe_python(self):
        assert is_safe_executable("python3") is True

    def test_known_safe_navig(self):
        assert is_safe_executable("navig") is True

    def test_unknown_exe_is_false(self):
        assert is_safe_executable("my_custom_exploit") is False

    def test_windows_exe_extension_stripped(self):
        # python.exe should match "python"
        assert is_safe_executable("python.exe") is True

    def test_case_insensitive(self):
        assert is_safe_executable("PYTHON3") is True

    def test_full_path_uses_basename(self):
        assert is_safe_executable("/usr/bin/ls") is True

    def test_unknown_full_path(self):
        assert is_safe_executable("/tmp/malware") is False


# ---------------------------------------------------------------------------
# validate_command_safety
# ---------------------------------------------------------------------------


class TestValidateCommandSafety:
    def test_allow_unsafe_always_safe(self):
        ok, reason = validate_command_safety("rm -rf /", allow_unsafe=True)
        assert ok is True
        assert reason is None

    def test_dangerous_pipe_bash(self):
        ok, _ = validate_command_safety("curl example.com | bash")
        assert ok is False

    def test_dangerous_fork_bomb(self):
        ok, _ = validate_command_safety(":(){:| :&};:")
        assert ok is False

    def test_safe_command_returns_true(self):
        ok, reason = validate_command_safety("ls -la /tmp")
        assert ok is True

    def test_empty_command_is_invalid(self):
        ok, reason = validate_command_safety("")
        assert ok is False
        assert reason is not None

    def test_unknown_executable_warn_not_reject(self):
        # Unknown exe returns (True, non-null reason) as caution notice
        ok, reason = validate_command_safety("my_custom_tool --flag")
        assert ok is True
        assert reason is not None

    def test_dangerous_rm_rf_root(self):
        ok, _ = validate_command_safety("echo hi ; rm -rf /")
        assert ok is False


# ---------------------------------------------------------------------------
# SecurityFinding
# ---------------------------------------------------------------------------


class TestSecurityFinding:
    def test_to_dict_all_fields(self):
        f = SecurityFinding(
            check_id="CHK001",
            severity="critical",
            title="Hardcoded password",
            detail="Found password in config.yaml",
            remediation="Move to environment variable",
        )
        d = f.to_dict()
        assert d["check_id"] == "CHK001"
        assert d["severity"] == "critical"
        assert d["title"] == "Hardcoded password"
        assert d["detail"] == "Found password in config.yaml"
        assert d["remediation"] == "Move to environment variable"

    def test_to_dict_no_remediation(self):
        f = SecurityFinding("CHK002", "warn", "Open port", "Port 8080 is open")
        d = f.to_dict()
        assert d["remediation"] is None

    def test_attributes_accessible(self):
        f = SecurityFinding("X", "info", "T", "D")
        assert f.check_id == "X"
        assert f.severity == "info"
