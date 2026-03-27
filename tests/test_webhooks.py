"""Unit tests for the webhooks module."""

import hashlib
import hmac
from datetime import datetime

import pytest

from navig.webhooks.receiver import WebhookEvent, WebhookReceiver, WebhookSourceConfig
from navig.webhooks.signatures import (
    SignatureConfig,
    extract_event_type,
    verify_github_signature,
    verify_signature,
)


class TestSignatureVerification:
    """Tests for signature verification functions."""

    def test_github_signature_valid(self):
        """Valid GitHub signature should verify."""
        secret = "test-secret"
        body = b'{"action": "push"}'

        # Calculate expected signature
        signature = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )

        result = verify_github_signature(
            body=body,
            signature=signature,
            secret=secret,
        )

        assert result is True

    def test_github_signature_invalid(self):
        """Invalid GitHub signature should fail."""
        result = verify_github_signature(
            body=b'{"action": "push"}',
            signature="sha256=invalid",
            secret="test-secret",
        )

        assert result is False

    def test_github_signature_empty(self):
        """Empty signature should fail."""
        result = verify_github_signature(
            body=b"test",
            signature="",
            secret="secret",
        )

        assert result is False

    def test_signature_config_for_github(self):
        """GitHub signature config should have correct values."""
        config = SignatureConfig.for_github()

        assert config.header == "X-Hub-Signature-256"
        assert config.algorithm == "sha256"
        assert config.prefix == "sha256="

    def test_signature_config_for_stripe(self):
        """Stripe signature config should have correct values."""
        config = SignatureConfig.for_stripe()

        assert config.header == "Stripe-Signature"
        assert config.algorithm == "sha256"

    def test_signature_config_for_gitlab(self):
        """GitLab signature config should have correct values."""
        config = SignatureConfig.for_gitlab()

        assert config.header == "X-Gitlab-Token"
        assert config.algorithm == "plain"

    def test_verify_signature_with_config(self):
        """Generic verify_signature should work with config."""
        secret = "my-secret"
        body = b'{"ref": "refs/heads/main"}'
        config = SignatureConfig.for_github()

        signature = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )

        result = verify_signature(body, signature, secret, config)

        assert result is True


class TestEventTypeExtraction:
    """Tests for event type extraction."""

    def test_github_push_event(self):
        """Should extract GitHub push event type from headers."""
        headers = {"X-GitHub-Event": "push"}
        payload = {"ref": "refs/heads/main", "commits": []}

        event_type = extract_event_type("github", headers, payload)

        assert event_type == "push"

    def test_github_event_lowercase_header(self):
        """Should handle lowercase header."""
        headers = {"x-github-event": "pull_request"}
        payload = {}

        event_type = extract_event_type("github", headers, payload)

        assert event_type == "pull_request"

    def test_stripe_event_type(self):
        """Should extract Stripe event type from payload."""
        headers = {}
        payload = {"type": "payment_intent.succeeded", "data": {}}

        event_type = extract_event_type("stripe", headers, payload)

        assert event_type == "payment_intent.succeeded"

    def test_gitlab_event_type(self):
        """Should extract GitLab event type from headers."""
        headers = {"X-Gitlab-Event": "Push Hook"}
        payload = {"object_kind": "push"}

        event_type = extract_event_type("gitlab", headers, payload)

        assert event_type == "Push Hook"

    def test_generic_event_type_from_payload(self):
        """Generic provider should use payload type."""
        headers = {}
        payload = {"event_type": "custom_event"}

        event_type = extract_event_type("custom", headers, payload)

        assert event_type == "custom_event"


class TestWebhookEvent:
    """Tests for WebhookEvent dataclass."""

    def test_event_creation(self):
        """Event should be created with all fields."""
        now = datetime.now()
        event = WebhookEvent(
            id="evt_123",
            source="github",
            event_type="push",
            payload={"ref": "refs/heads/main"},
            headers={"X-GitHub-Event": "push"},
            received_at=now,
        )

        assert event.id == "evt_123"
        assert event.source == "github"
        assert event.event_type == "push"
        assert event.received_at == now

    def test_event_to_dict(self):
        """Event should serialize to dict."""
        now = datetime.now()
        event = WebhookEvent(
            id="evt_456",
            source="stripe",
            event_type="charge.succeeded",
            payload={"amount": 1000},
            headers={},
            received_at=now,
        )

        data = event.to_dict()

        assert data["id"] == "evt_456"
        assert data["source"] == "stripe"
        assert data["event_type"] == "charge.succeeded"
        assert "payload" in data


class TestWebhookSourceConfig:
    """Tests for WebhookSourceConfig dataclass."""

    def test_config_creation(self):
        """Config should be created with defaults."""
        config = WebhookSourceConfig(
            name="github-main",
            secret="webhook_secret",
        )

        assert config.name == "github-main"
        assert config.secret == "webhook_secret"
        assert config.enabled is True

    def test_config_with_signature_header(self):
        """Config should accept signature header."""
        config = WebhookSourceConfig(
            name="custom",
            secret="s",
            signature_header="X-Custom-Sig",
        )

        assert config.signature_header == "X-Custom-Sig"

    def test_config_get_signature_config(self):
        """Config should generate SignatureConfig."""
        config = WebhookSourceConfig(
            name="test",
            secret="secret",
            signature_header="X-Signature",
            signature_algo="sha256",
        )

        sig_config = config.get_signature_config()

        assert sig_config is not None
        assert sig_config.header == "X-Signature"
        assert sig_config.algorithm == "sha256"


class TestWebhookReceiver:
    """Tests for WebhookReceiver class."""

    @pytest.fixture
    def receiver(self):
        """Create test receiver."""
        return WebhookReceiver(
            {
                "webhooks": {
                    "enabled": True,
                    "path_prefix": "/webhook",
                }
            }
        )

    def test_receiver_creation(self, receiver):
        """Receiver should be created with config."""
        assert receiver.enabled is True
        assert receiver.path_prefix == "/webhook"

    def test_get_routes(self, receiver):
        """Should return aiohttp routes."""
        routes = receiver.get_routes()

        # Should have routes
        assert len(routes) >= 1

    def test_register_handler(self, receiver):
        """Should register event handler."""

        async def handler(event):
            pass

        receiver.on_event(handler)

        assert handler in receiver._handlers

    def test_history_tracking(self, receiver):
        """Receiver should track history config."""
        assert hasattr(receiver, "_recent_events")
        assert receiver._max_history == 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
