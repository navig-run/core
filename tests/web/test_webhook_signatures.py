"""Hermetic unit tests for navig.webhooks.signatures."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from navig.webhooks.signatures import (
    SignatureConfig,
    extract_event_type,
    verify_github_signature,
    verify_signature,
    verify_stripe_signature,
)


def _make_hmac(body: bytes, secret: str, algorithm: str = "sha256") -> str:
    alg = hashlib.sha256 if algorithm == "sha256" else hashlib.sha1
    return hmac.new(secret.encode(), body, alg).hexdigest()


# ---------------------------------------------------------------------------
# SignatureConfig factory methods
# ---------------------------------------------------------------------------


class TestSignatureConfigFactories:
    def test_github_header(self):
        cfg = SignatureConfig.for_github()
        assert cfg.header == "X-Hub-Signature-256"
        assert cfg.algorithm == "sha256"
        assert cfg.prefix == "sha256="

    def test_stripe_header(self):
        cfg = SignatureConfig.for_stripe()
        assert cfg.header == "Stripe-Signature"
        assert cfg.algorithm == "sha256"
        assert cfg.prefix == ""

    def test_gitlab_header(self):
        cfg = SignatureConfig.for_gitlab()
        assert cfg.header == "X-Gitlab-Token"
        assert cfg.algorithm == "plain"


# ---------------------------------------------------------------------------
# verify_signature — sha256 path
# ---------------------------------------------------------------------------


class TestVerifySignatureSha256:
    _SECRET = "mysecret"
    _BODY = b'{"event":"push"}'

    def _sig(self) -> str:
        return _make_hmac(self._BODY, self._SECRET)

    def test_valid_signature_passes(self):
        cfg = SignatureConfig(header="X-Sig", algorithm="sha256")
        assert verify_signature(self._BODY, self._sig(), self._SECRET, cfg)

    def test_invalid_signature_fails(self):
        cfg = SignatureConfig(header="X-Sig", algorithm="sha256")
        assert not verify_signature(self._BODY, "deadbeef" * 8, self._SECRET, cfg)

    def test_prefix_stripped(self):
        cfg = SignatureConfig(header="X-Sig", algorithm="sha256", prefix="sha256=")
        assert verify_signature(self._BODY, f"sha256={self._sig()}", self._SECRET, cfg)

    def test_empty_signature_fails(self):
        cfg = SignatureConfig(header="X-Sig", algorithm="sha256")
        assert not verify_signature(self._BODY, "", self._SECRET, cfg)

    def test_empty_secret_fails(self):
        cfg = SignatureConfig(header="X-Sig", algorithm="sha256")
        assert not verify_signature(self._BODY, self._sig(), "", cfg)


# ---------------------------------------------------------------------------
# verify_signature — sha1 path
# ---------------------------------------------------------------------------


class TestVerifySignatureSha1:
    _SECRET = "s1secret"
    _BODY = b"payload"

    def _sig(self) -> str:
        return _make_hmac(self._BODY, self._SECRET, "sha1")

    def test_valid_sha1_signature(self):
        cfg = SignatureConfig(header="X-Sig", algorithm="sha1")
        assert verify_signature(self._BODY, self._sig(), self._SECRET, cfg)

    def test_wrong_sha1_signature(self):
        cfg = SignatureConfig(header="X-Sig", algorithm="sha1")
        assert not verify_signature(self._BODY, "wrong", self._SECRET, cfg)


# ---------------------------------------------------------------------------
# verify_signature — plain token (GitLab)
# ---------------------------------------------------------------------------


class TestVerifySignaturePlain:
    def test_matching_token(self):
        cfg = SignatureConfig.for_gitlab()
        assert verify_signature(b"ignored", "mytoken", "mytoken", cfg)

    def test_mismatched_token(self):
        cfg = SignatureConfig.for_gitlab()
        assert not verify_signature(b"ignored", "wrongtoken", "mytoken", cfg)


# ---------------------------------------------------------------------------
# verify_github_signature convenience wrapper
# ---------------------------------------------------------------------------


class TestVerifyGithubSignature:
    _SECRET = "ghsecret"
    _BODY = b'{"ref":"refs/heads/main"}'

    def test_valid(self):
        sig = "sha256=" + _make_hmac(self._BODY, self._SECRET)
        assert verify_github_signature(self._BODY, sig, self._SECRET)

    def test_invalid(self):
        assert not verify_github_signature(self._BODY, "sha256=bad", self._SECRET)


# ---------------------------------------------------------------------------
# extract_event_type
# ---------------------------------------------------------------------------


class TestExtractEventType:
    def test_github_from_header(self):
        assert extract_event_type("github", {"X-GitHub-Event": "push"}, {}) == "push"

    def test_github_lowercase_header(self):
        assert extract_event_type("github", {"x-github-event": "pull_request"}, {}) == "pull_request"

    def test_gitlab_from_header(self):
        assert extract_event_type("gitlab", {"X-Gitlab-Event": "merge_request"}, {}) == "merge_request"

    def test_stripe_from_payload(self):
        assert extract_event_type("stripe", {}, {"type": "charge.succeeded"}) == "charge.succeeded"

    def test_slack_nested_type(self):
        payload = {"event": {"type": "message"}}
        assert extract_event_type("slack", {}, payload) == "message"

    def test_slack_top_level_type(self):
        payload = {"type": "url_verification"}
        assert extract_event_type("slack", {}, payload) == "url_verification"

    def test_generic_event_type(self):
        assert extract_event_type("custom", {}, {"event_type": "created"}) == "created"

    def test_generic_event(self):
        assert extract_event_type("custom", {}, {"event": "updated"}) == "updated"

    def test_generic_type(self):
        assert extract_event_type("custom", {}, {"type": "deleted"}) == "deleted"

    def test_unknown_fallback(self):
        assert extract_event_type("unknown_source", {}, {}) == "unknown"
