"""
Batch 112: hermetic tests for
  - navig/webhooks/signatures.py   (SignatureConfig, verify_*, extract_event_type)
  - navig/webhooks/receiver.py     (WebhookEvent, WebhookSourceConfig, WebhookReceiver)
"""
from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from unittest.mock import patch


# ---------------------------------------------------------------------------
# SignatureConfig
# ---------------------------------------------------------------------------

class TestSignatureConfig:
    def test_for_github(self):
        from navig.webhooks.signatures import SignatureConfig
        cfg = SignatureConfig.for_github()
        assert cfg.header == "X-Hub-Signature-256"
        assert cfg.algorithm == "sha256"
        assert cfg.prefix == "sha256="

    def test_for_stripe(self):
        from navig.webhooks.signatures import SignatureConfig
        cfg = SignatureConfig.for_stripe()
        assert cfg.header == "Stripe-Signature"
        assert cfg.prefix == ""

    def test_for_gitlab(self):
        from navig.webhooks.signatures import SignatureConfig
        cfg = SignatureConfig.for_gitlab()
        assert cfg.header == "X-Gitlab-Token"
        assert cfg.algorithm == "plain"

    def test_custom_construction(self):
        from navig.webhooks.signatures import SignatureConfig
        cfg = SignatureConfig(header="X-Custom", algorithm="sha1", prefix="sha1=")
        assert cfg.header == "X-Custom"
        assert cfg.algorithm == "sha1"


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------

class TestVerifySignature:
    def _cfg(self, algo="sha256", prefix=""):
        from navig.webhooks.signatures import SignatureConfig
        return SignatureConfig(header="X-Sig", algorithm=algo, prefix=prefix)

    def test_empty_signature_returns_false(self):
        from navig.webhooks.signatures import verify_signature
        assert verify_signature(b"body", "", "secret", self._cfg()) is False

    def test_empty_secret_returns_false(self):
        from navig.webhooks.signatures import verify_signature
        assert verify_signature(b"body", "abc123", "", self._cfg()) is False

    def test_plain_match(self):
        from navig.webhooks.signatures import verify_signature
        assert verify_signature(b"body", "mysecret", "mysecret", self._cfg(algo="plain")) is True

    def test_plain_mismatch(self):
        from navig.webhooks.signatures import verify_signature
        assert verify_signature(b"body", "wrong", "mysecret", self._cfg(algo="plain")) is False

    def test_sha256_valid(self):
        from navig.webhooks.signatures import verify_signature
        body = b"hello world"
        secret = "mysecret"
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_signature(body, expected, secret, self._cfg(algo="sha256")) is True

    def test_sha256_invalid(self):
        from navig.webhooks.signatures import verify_signature
        assert verify_signature(b"hello", "deadbeef" * 8, "secret", self._cfg(algo="sha256")) is False

    def test_sha1_valid(self):
        from navig.webhooks.signatures import verify_signature
        body = b"test"
        secret = "key"
        expected = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
        assert verify_signature(body, expected, secret, self._cfg(algo="sha1")) is True

    def test_unknown_algo_returns_false(self):
        from navig.webhooks.signatures import verify_signature
        assert verify_signature(b"body", "sig", "secret", self._cfg(algo="md5")) is False

    def test_prefix_stripped_before_comparison(self):
        from navig.webhooks.signatures import verify_signature
        body = b"data"
        secret = "s"
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        from navig.webhooks.signatures import SignatureConfig
        cfg = SignatureConfig(header="X-Sig", algorithm="sha256", prefix="sha256=")
        assert verify_signature(body, expected, secret, cfg) is True


# ---------------------------------------------------------------------------
# verify_github_signature
# ---------------------------------------------------------------------------

class TestVerifyGithubSignature:
    def test_valid_signature(self):
        from navig.webhooks.signatures import verify_github_signature
        body = b"payload"
        secret = "ghsecret"
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_github_signature(body, sig, secret) is True

    def test_invalid_signature(self):
        from navig.webhooks.signatures import verify_github_signature
        assert verify_github_signature(b"payload", "sha256=bad", "ghsecret") is False

    def test_empty_signature(self):
        from navig.webhooks.signatures import verify_github_signature
        assert verify_github_signature(b"payload", "", "ghsecret") is False


# ---------------------------------------------------------------------------
# verify_stripe_signature
# ---------------------------------------------------------------------------

class TestVerifyStripeSignature:
    def _build_header(self, body: bytes, secret: str, ts: int | None = None) -> str:
        if ts is None:
            ts = int(time.time())
        signed = f"{ts}.{body.decode()}"
        sig = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return f"t={ts},v1={sig}"

    def test_empty_header_returns_false(self):
        from navig.webhooks.signatures import verify_stripe_signature
        assert verify_stripe_signature(b"body", "", "secret") is False

    def test_missing_parts_returns_false(self):
        from navig.webhooks.signatures import verify_stripe_signature
        assert verify_stripe_signature(b"body", "t=1234", "secret") is False

    def test_valid_signature(self):
        from navig.webhooks.signatures import verify_stripe_signature
        body = b'{"type":"payment_intent.created"}'
        secret = "whsec_test"
        header = self._build_header(body, secret)
        assert verify_stripe_signature(body, header, secret) is True

    def test_expired_timestamp_returns_false(self):
        from navig.webhooks.signatures import verify_stripe_signature
        body = b"payload"
        secret = "secret"
        old_ts = int(time.time()) - 600  # 10 minutes old
        header = self._build_header(body, secret, ts=old_ts)
        assert verify_stripe_signature(body, header, secret, tolerance=300) is False

    def test_invalid_timestamp_returns_false(self):
        from navig.webhooks.signatures import verify_stripe_signature
        assert verify_stripe_signature(b"body", "t=notanumber,v1=abc", "secret") is False


# ---------------------------------------------------------------------------
# extract_event_type
# ---------------------------------------------------------------------------

class TestExtractEventType:
    def _fn(self):
        from navig.webhooks.signatures import extract_event_type
        return extract_event_type

    def test_github_from_header(self):
        r = self._fn()("github", {"X-GitHub-Event": "push"}, {})
        assert r == "push"

    def test_github_lowercase_header(self):
        r = self._fn()("github", {"x-github-event": "pull_request"}, {})
        assert r == "pull_request"

    def test_github_missing_header(self):
        r = self._fn()("github", {}, {})
        assert r == "unknown"

    def test_gitlab_from_header(self):
        r = self._fn()("gitlab", {"X-Gitlab-Event": "Push Hook"}, {})
        assert r == "Push Hook"

    def test_stripe_from_payload(self):
        r = self._fn()("stripe", {}, {"type": "charge.succeeded"})
        assert r == "charge.succeeded"

    def test_stripe_missing(self):
        r = self._fn()("stripe", {}, {})
        assert r == "unknown"

    def test_slack_nested_event(self):
        r = self._fn()("slack", {}, {"event": {"type": "message"}})
        assert r == "message"

    def test_slack_fallback_type(self):
        r = self._fn()("slack", {}, {"type": "app_mention"})
        assert r == "app_mention"

    def test_generic_event_type_key(self):
        r = self._fn()("custom", {}, {"event_type": "my.event"})
        assert r == "my.event"

    def test_generic_event_key(self):
        r = self._fn()("custom", {}, {"event": "fired"})
        assert r == "fired"

    def test_generic_type_key(self):
        r = self._fn()("custom", {}, {"type": "foo"})
        assert r == "foo"

    def test_generic_fallback_unknown(self):
        r = self._fn()("custom", {}, {})
        assert r == "unknown"


# ---------------------------------------------------------------------------
# WebhookEvent
# ---------------------------------------------------------------------------

class TestWebhookEvent:
    def _make(self, **kw):
        from navig.webhooks.receiver import WebhookEvent
        defaults = dict(
            id="evt-1",
            source="github",
            event_type="push",
            payload={"ref": "main"},
            headers={"X-GitHub-Event": "push"},
            received_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
        defaults.update(kw)
        return WebhookEvent(**defaults)

    def test_to_dict_has_id(self):
        ev = self._make()
        d = ev.to_dict()
        assert d["id"] == "evt-1"
        assert d["source"] == "github"
        assert d["event_type"] == "push"

    def test_to_dict_received_at_isoformat(self):
        ev = self._make()
        d = ev.to_dict()
        assert "2024" in d["received_at"]

    def test_signature_valid_none_by_default(self):
        ev = self._make()
        assert ev.signature_valid is None

    def test_signature_valid_set(self):
        ev = self._make(signature_valid=True)
        assert ev.signature_valid is True
        assert ev.to_dict()["signature_valid"] is True


# ---------------------------------------------------------------------------
# WebhookSourceConfig
# ---------------------------------------------------------------------------

class TestWebhookSourceConfig:
    def test_defaults(self):
        from navig.webhooks.receiver import WebhookSourceConfig
        cfg = WebhookSourceConfig(name="test")
        assert cfg.enabled is True
        assert cfg.secret is None
        assert cfg.verify_signature is True

    def test_get_signature_config_no_header(self):
        from navig.webhooks.receiver import WebhookSourceConfig
        cfg = WebhookSourceConfig(name="test")
        assert cfg.get_signature_config() is None

    def test_get_signature_config_verify_false(self):
        from navig.webhooks.receiver import WebhookSourceConfig
        cfg = WebhookSourceConfig(name="test", signature_header="X-Sig", verify_signature=False)
        assert cfg.get_signature_config() is None

    def test_get_signature_config_with_header(self):
        from navig.webhooks.receiver import WebhookSourceConfig
        cfg = WebhookSourceConfig(name="test", signature_header="X-Sig", signature_algo="sha256")
        sig_cfg = cfg.get_signature_config()
        assert sig_cfg is not None
        assert sig_cfg.header == "X-Sig"
        assert sig_cfg.algorithm == "sha256"


# ---------------------------------------------------------------------------
# WebhookReceiver
# ---------------------------------------------------------------------------

class TestWebhookReceiver:
    def test_default_sources_loaded(self):
        from navig.webhooks.receiver import WebhookReceiver
        r = WebhookReceiver()
        assert "github" in r._sources
        assert "stripe" in r._sources
        assert "gitlab" in r._sources
        assert "custom" in r._sources

    def test_enabled_by_default(self):
        from navig.webhooks.receiver import WebhookReceiver
        r = WebhookReceiver()
        assert r.enabled is True

    def test_path_prefix_default(self):
        from navig.webhooks.receiver import WebhookReceiver
        r = WebhookReceiver()
        assert r.path_prefix == "/webhook"

    def test_custom_path_prefix(self):
        from navig.webhooks.receiver import WebhookReceiver
        r = WebhookReceiver({"webhooks": {"path_prefix": "/hooks"}})
        assert r.path_prefix == "/hooks"

    def test_on_event_registers_handler(self):
        from navig.webhooks.receiver import WebhookReceiver
        r = WebhookReceiver()
        called = []
        @r.on_event
        def handler(ev):
            called.append(ev)
        assert handler in r._handlers

    def test_on_event_returns_handler(self):
        from navig.webhooks.receiver import WebhookReceiver
        r = WebhookReceiver()
        fn = lambda e: None
        result = r.on_event(fn)
        assert result is fn

    def test_env_var_secret_resolved(self):
        from navig.webhooks.receiver import WebhookReceiver
        import os
        os.environ["TEST_WEBHOOK_SECRET"] = "mysecret123"
        try:
            r = WebhookReceiver({
                "webhooks": {
                    "secrets": {"github": "${TEST_WEBHOOK_SECRET}"},
                    "sources": {"github": {"enabled": True, "signature_header": "X-Hub-Signature-256"}}
                }
            })
            assert r._sources["github"].secret == "mysecret123"
        finally:
            del os.environ["TEST_WEBHOOK_SECRET"]

    def test_recent_events_starts_empty(self):
        from navig.webhooks.receiver import WebhookReceiver
        r = WebhookReceiver()
        assert r._recent_events == []

    def test_max_history_default(self):
        from navig.webhooks.receiver import WebhookReceiver
        r = WebhookReceiver()
        assert r._max_history == 100
