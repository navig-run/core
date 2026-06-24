"""
Batch 48 — hermetic unit tests for:
  navig/vault/secret_str.py                    — SecretStr, mask_secret
  navig/webhooks/signatures.py                 — SignatureConfig, verify_signature, extract_event_type
  navig/connectors/circuit_breaker.py          — CircuitBreaker state machine
  navig/tools/output_validator.py              — validate_output, OutputValidationError
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# navig/vault/secret_str.py — SecretStr & mask_secret
# ---------------------------------------------------------------------------

from navig.vault.secret_str import SecretStr, Secret, mask_secret


class TestSecretStrBasic:
    def test_str_is_redacted(self):
        s = SecretStr("top-secret")
        assert str(s) == "***"

    def test_repr_is_redacted(self):
        s = SecretStr("top-secret")
        assert repr(s) == "SecretStr('***')"

    def test_format_redacted(self):
        s = SecretStr("key")
        assert f"{s}" == "***"

    def test_reveal_returns_value(self):
        s = SecretStr("my-key")
        assert s.reveal() == "my-key"

    def test_len_reflects_actual_value(self):
        s = SecretStr("abc")
        assert len(s) == 3

    def test_bool_true_for_nonempty(self):
        assert bool(SecretStr("x")) is True

    def test_bool_false_for_empty(self):
        assert bool(SecretStr("")) is False

    def test_equality_same_value(self):
        a = SecretStr("key")
        b = SecretStr("key")
        assert a == b

    def test_inequality_different_value(self):
        a = SecretStr("key1")
        b = SecretStr("key2")
        assert a != b

    def test_not_equal_to_raw_string(self):
        s = SecretStr("key")
        assert s != "key"

    def test_hash_same_for_equal(self):
        a = SecretStr("x")
        b = SecretStr("x")
        assert hash(a) == hash(b)

    def test_hash_different_for_different(self):
        a = SecretStr("x")
        b = SecretStr("y")
        assert hash(a) != hash(b)

    def test_copy_is_equal_but_distinct(self):
        original = SecretStr("secret")
        copied = original.copy()
        assert copied == original
        assert copied is not original

    def test_type_error_on_non_string(self):
        with pytest.raises(TypeError):
            SecretStr(12345)  # type: ignore[arg-type]

    def test_usable_as_dict_key(self):
        d = {SecretStr("k"): "val"}
        assert d[SecretStr("k")] == "val"

    def test_secret_alias(self):
        assert Secret is SecretStr


class TestSecretStrRevealPrefix:
    def test_reveal_prefix_shows_n_chars(self):
        s = SecretStr("sk-abcdef123456")
        result = s.reveal_prefix(4)
        assert result.startswith("sk-a")
        assert "***" in result

    def test_reveal_prefix_short_value_fully_masked(self):
        s = SecretStr("abc")
        assert s.reveal_prefix(10) == "***"

    def test_reveal_prefix_default_4_chars(self):
        s = SecretStr("1234567890")
        result = s.reveal_prefix()
        assert result.startswith("1234")


class TestSecretStrFromEnv:
    def test_reads_env_variable(self):
        with patch.dict(os.environ, {"TEST_SECRET_VAR": "env-value"}):
            s = SecretStr.from_env("TEST_SECRET_VAR")
            assert s.reveal() == "env-value"

    def test_default_when_missing(self):
        s = SecretStr.from_env("__UNDEFINED_VAR__", default="fallback")
        assert s.reveal() == "fallback"

    def test_empty_default_when_not_set(self):
        s = SecretStr.from_env("__UNDEFINED_VAR__")
        assert s.reveal() == ""


class TestMaskSecret:
    def test_none_returns_none_marker(self):
        assert mask_secret(None) == "<none>"

    def test_secretstr_uses_reveal_prefix(self):
        s = SecretStr("sk-abcdef")
        result = mask_secret(s)
        assert result.startswith("sk-a")

    def test_secretstr_zero_prefix_fully_masked(self):
        s = SecretStr("key")
        result = mask_secret(s, show_prefix=0)
        assert result == "***"

    def test_plain_string_masked(self):
        result = mask_secret("abcdefgh", show_prefix=4)
        assert result.startswith("abcd")
        assert "***" in result

    def test_empty_string_returns_empty_marker(self):
        assert mask_secret("") == "<empty>"

    def test_short_plain_string_full_mask(self):
        result = mask_secret("ab", show_prefix=4)
        assert result == "***"


# ---------------------------------------------------------------------------
# navig/webhooks/signatures.py — SignatureConfig + verify_signature
# ---------------------------------------------------------------------------

from navig.webhooks.signatures import (
    SignatureConfig,
    verify_signature,
    verify_github_signature,
    extract_event_type,
)


class TestSignatureConfig:
    def test_github_header(self):
        cfg = SignatureConfig.for_github()
        assert cfg.header == "X-Hub-Signature-256"

    def test_github_algorithm_sha256(self):
        cfg = SignatureConfig.for_github()
        assert cfg.algorithm == "sha256"

    def test_github_prefix(self):
        cfg = SignatureConfig.for_github()
        assert cfg.prefix == "sha256="

    def test_stripe_header(self):
        cfg = SignatureConfig.for_stripe()
        assert cfg.header == "Stripe-Signature"

    def test_stripe_algorithm(self):
        cfg = SignatureConfig.for_stripe()
        assert cfg.algorithm == "sha256"

    def test_gitlab_algorithm_plain(self):
        cfg = SignatureConfig.for_gitlab()
        assert cfg.algorithm == "plain"

    def test_gitlab_header(self):
        cfg = SignatureConfig.for_gitlab()
        assert cfg.header == "X-Gitlab-Token"


def _make_sig(body: bytes, secret: str, algo: str = "sha256") -> str:
    h = hashlib.sha256 if algo == "sha256" else hashlib.sha1
    return hmac.new(secret.encode(), body, h).hexdigest()


class TestVerifySignature:
    def test_valid_sha256(self):
        body = b"hello"
        secret = "my-secret"
        sig = _make_sig(body, secret, "sha256")
        cfg = SignatureConfig(header="X-Sig", algorithm="sha256")
        assert verify_signature(body, sig, secret, cfg) is True

    def test_invalid_sha256(self):
        body = b"hello"
        cfg = SignatureConfig(header="X-Sig", algorithm="sha256")
        assert verify_signature(body, "wrong-sig", "my-secret", cfg) is False

    def test_sha256_with_prefix_stripped(self):
        body = b"payload"
        secret = "sec"
        raw_sig = _make_sig(body, secret, "sha256")
        prefixed = f"sha256={raw_sig}"
        cfg = SignatureConfig(header="X", algorithm="sha256", prefix="sha256=")
        assert verify_signature(body, prefixed, secret, cfg) is True

    def test_sha1_valid(self):
        body = b"data"
        secret = "s"
        sig = _make_sig(body, secret, "sha1")
        cfg = SignatureConfig(header="X", algorithm="sha1")
        assert verify_signature(body, sig, secret, cfg) is True

    def test_empty_signature_returns_false(self):
        cfg = SignatureConfig.for_github()
        assert verify_signature(b"body", "", "secret", cfg) is False

    def test_empty_secret_returns_false(self):
        cfg = SignatureConfig.for_github()
        assert verify_signature(body=b"body", signature="sha256=abc", secret="", config=cfg) is False

    def test_plain_algorithm_matching_token(self):
        cfg = SignatureConfig.for_gitlab()
        assert verify_signature(b"any", "my-token", "my-token", cfg) is True

    def test_plain_algorithm_wrong_token(self):
        cfg = SignatureConfig.for_gitlab()
        assert verify_signature(b"any", "wrong", "my-token", cfg) is False

    def test_unknown_algorithm_returns_false(self):
        cfg = SignatureConfig(header="X", algorithm="md5")
        assert verify_signature(b"data", "abc", "secret", cfg) is False


class TestVerifyGithubSignature:
    def test_correct_signature(self):
        body = b"github-body"
        secret = "gh-secret"
        raw = _make_sig(body, secret, "sha256")
        signature = f"sha256={raw}"
        assert verify_github_signature(body, signature, secret) is True

    def test_wrong_signature(self):
        assert verify_github_signature(b"body", "sha256=bad", "secret") is False


class TestExtractEventType:
    def test_github_event(self):
        headers = {"X-GitHub-Event": "push"}
        result = extract_event_type("github", headers, {})
        assert result == "push"

    def test_gitlab_event(self):
        headers = {"X-Gitlab-Event": "merge_request"}
        result = extract_event_type("gitlab", headers, {})
        assert result == "merge_request"

    def test_stripe_event(self):
        payload = {"type": "payment_intent.succeeded"}
        result = extract_event_type("stripe", {}, payload)
        assert result == "payment_intent.succeeded"

    def test_slack_event(self):
        payload = {"event": {"type": "message"}}
        result = extract_event_type("slack", {}, payload)
        assert result == "message"

    def test_generic_unknown(self):
        result = extract_event_type("custom", {}, {})
        assert result == "unknown"

    def test_generic_with_event_type_key(self):
        result = extract_event_type("custom", {}, {"event_type": "deploy"})
        assert result == "deploy"


# ---------------------------------------------------------------------------
# navig/connectors/circuit_breaker.py — CircuitBreaker
# ---------------------------------------------------------------------------

from navig.connectors.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreakerInitial:
    def test_starts_closed(self):
        cb = CircuitBreaker("svc")
        assert cb.state == CircuitState.CLOSED

    def test_allow_request_when_closed(self):
        cb = CircuitBreaker("svc")
        assert cb.allow_request() is True

    def test_connector_id_stored(self):
        cb = CircuitBreaker("gmail")
        assert cb.connector_id == "gmail"

    def test_custom_thresholds(self):
        cb = CircuitBreaker("svc", failure_threshold=5, recovery_timeout=60.0)
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 60.0


class TestCircuitBreakerSuccess:
    def test_record_success_stays_closed(self):
        cb = CircuitBreaker("svc")
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_record_success_resets_failure_count(self):
        cb = CircuitBreaker("svc", failure_threshold=3)
        cb.record_failure()
        cb.record_success()
        assert cb._failure_count == 0


class TestCircuitBreakerTrip:
    def test_trips_on_threshold(self):
        cb = CircuitBreaker("svc", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_open_rejects_requests(self):
        cb = CircuitBreaker("svc", failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is False

    def test_total_trips_incremented(self):
        cb = CircuitBreaker("svc", failure_threshold=1)
        cb.record_failure()
        assert cb._total_trips == 1

    def test_second_trip_increments_again(self):
        cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout=0)
        cb.record_failure()
        cb.record_success()  # CLOSED
        cb.record_failure()   # trip again
        assert cb._total_trips == 2


class TestCircuitBreakerHalfOpen:
    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout=0.0)
        cb.record_failure()  # → OPEN
        # recovery_timeout=0 means any elapsed time triggers HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_allows_one_request(self):
        cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout=0.0)
        cb.record_failure()
        assert cb.allow_request() is True

    def test_half_open_success_closes(self):
        cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout=0.0)
        cb.record_failure()
        cb.state  # trigger promotion to HALF_OPEN
        cb.record_success()
        assert cb._state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout=0.0)
        cb.record_failure()
        cb.state  # trigger promotion to HALF_OPEN
        cb.record_failure()
        assert cb._state == CircuitState.OPEN


class TestCircuitBreakerReset:
    def test_reset_clears_failures(self):
        cb = CircuitBreaker("svc", failure_threshold=2)
        cb.record_failure()
        cb.reset()
        assert cb._failure_count == 0
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerToDict:
    def test_to_dict_contains_keys(self):
        cb = CircuitBreaker("api", failure_threshold=3, recovery_timeout=30.0)
        d = cb.to_dict()
        assert d["connector_id"] == "api"
        assert d["state"] == "closed"
        assert d["failure_count"] == 0
        assert "total_trips" in d
        assert d["recovery_timeout"] == 30.0

    def test_to_dict_state_after_trip(self):
        cb = CircuitBreaker("api", failure_threshold=1)
        cb.record_failure()
        d = cb.to_dict()
        assert d["state"] == "open"


# ---------------------------------------------------------------------------
# navig/tools/output_validator.py — validate_output (naive fallback path)
# ---------------------------------------------------------------------------

from navig.tools.output_validator import (
    OutputValidationError,
    validate_output,
    _naive_check,
)


class TestNaiveCheck:
    def test_object_type_match(self):
        ok, msg = _naive_check({"a": 1}, {"type": "object"})
        assert ok is True and msg is None

    def test_object_type_mismatch(self):
        ok, msg = _naive_check([1, 2], {"type": "object"})
        assert ok is False

    def test_array_type_match(self):
        ok, msg = _naive_check([1, 2], {"type": "array"})
        assert ok is True

    def test_string_type_match(self):
        ok, msg = _naive_check("hello", {"type": "string"})
        assert ok is True

    def test_integer_type_match(self):
        ok, msg = _naive_check(42, {"type": "integer"})
        assert ok is True

    def test_boolean_type_match(self):
        ok, msg = _naive_check(True, {"type": "boolean"})
        assert ok is True

    def test_null_type_match(self):
        ok, msg = _naive_check(None, {"type": "null"})
        assert ok is True

    def test_required_field_present(self):
        ok, msg = _naive_check({"name": "Alice"}, {"type": "object", "required": ["name"]})
        assert ok is True

    def test_required_field_missing(self):
        ok, msg = _naive_check({}, {"type": "object", "required": ["name"]})
        assert ok is False
        assert "name" in msg

    def test_enum_valid(self):
        ok, msg = _naive_check("red", {"enum": ["red", "green", "blue"]})
        assert ok is True

    def test_enum_invalid(self):
        ok, msg = _naive_check("purple", {"enum": ["red", "green"]})
        assert ok is False

    def test_no_type_passes(self):
        ok, msg = _naive_check({"any": "thing"}, {})
        assert ok is True

    def test_unknown_type_passes(self):
        ok, msg = _naive_check("x", {"type": "unicorn"})
        assert ok is True


class TestValidateOutput:
    def test_valid_returns_true_none(self):
        ok, msg = validate_output({"key": "val"}, {"type": "object"})
        assert ok is True
        assert msg is None

    def test_invalid_returns_false_message(self):
        ok, msg = validate_output("not-a-dict", {"type": "object"})
        assert ok is False
        assert msg is not None

    def test_strict_mode_raises_on_failure(self):
        with pytest.raises(OutputValidationError):
            validate_output("wrong", {"type": "object"}, strict=True)

    def test_strict_mode_passes_silently(self):
        ok, msg = validate_output({"x": 1}, {"type": "object"}, strict=True)
        assert ok is True

    def test_output_validation_error_is_value_error(self):
        assert issubclass(OutputValidationError, ValueError)
