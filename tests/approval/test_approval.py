"""Unit tests for the approval module."""

import asyncio

import pytest

from navig.approval import (
    ApprovalLevel,
    ApprovalManager,
    ApprovalPolicy,
    ApprovalStatus,
)
from navig.approval.manager import ApprovalRequest

pytestmark = pytest.mark.integration


class TestApprovalPolicy:
    """Tests for ApprovalPolicy class."""

    def test_default_policy_has_patterns(self):
        """Default policy should have patterns for all levels."""
        policy = ApprovalPolicy.default()

        assert "safe" in policy.patterns
        assert "confirm" in policy.patterns
        assert "dangerous" in policy.patterns
        assert "never" in policy.patterns

    def test_classify_safe_command(self):
        """Safe commands should be classified as SAFE."""
        policy = ApprovalPolicy.default()

        # Use commands that match the default patterns
        level = policy.classify("host list")
        assert level == ApprovalLevel.SAFE

        level = policy.classify("app list")
        assert level == ApprovalLevel.SAFE

    def test_classify_dangerous_command(self):
        """Dangerous commands should be classified correctly."""
        policy = ApprovalPolicy.default()

        level = policy.classify("run rm something")
        assert level == ApprovalLevel.DANGEROUS

        level = policy.classify("db drop test")
        assert level == ApprovalLevel.DANGEROUS

    def test_classify_confirm_command(self):
        """Commands requiring confirmation should be classified."""
        policy = ApprovalPolicy.default()

        level = policy.classify("file remove test.txt")
        assert level == ApprovalLevel.CONFIRM

    def test_classify_never_command(self):
        """Commands that should never run are classified as NEVER."""
        policy = ApprovalPolicy.default()

        level = policy.classify("run rm -rf /")
        assert level == ApprovalLevel.NEVER

    def test_custom_policy(self):
        """Custom policies should work correctly."""
        policy = ApprovalPolicy(
            safe_patterns=["echo*", "pwd"],
            confirm_patterns=["custom-cmd*"],
            dangerous_patterns=[],
            never_patterns=[],
        )

        assert policy.classify("echo hello") == ApprovalLevel.SAFE
        assert policy.classify("custom-cmd --flag") == ApprovalLevel.CONFIRM
        # Unknown commands default to CONFIRM
        assert policy.classify("unknown-command") == ApprovalLevel.CONFIRM


class TestApprovalRequest:
    """Tests for ApprovalRequest dataclass."""

    def test_request_creation(self):
        """Request should be created with correct defaults."""
        request = ApprovalRequest(
            id="test123",
            command="rm -rf /tmp/test",
            level=ApprovalLevel.DANGEROUS,
            description="Remove temp dir",
            session_key="cli:default",
            channel="cli",
            user_id="testuser",
        )

        assert request.id == "test123"
        assert request.status == ApprovalStatus.PENDING
        assert request.created_at is not None

    def test_request_to_dict(self):
        """Request should serialize to dict."""
        request = ApprovalRequest(
            id="req456",
            command="systemctl restart nginx",
            level=ApprovalLevel.CONFIRM,
            description="Restart web server",
            session_key="cli:default",
            channel="cli",
            user_id="test-agent",
        )

        data = request.to_dict()

        assert data["command"] == "systemctl restart nginx"
        assert data["level"] == "confirm"
        assert data["description"] == "Restart web server"
        assert data["user_id"] == "test-agent"


class TestApprovalManager:
    """Tests for ApprovalManager class."""

    @pytest.fixture
    def manager(self):
        """Create approval manager for tests."""
        policy = ApprovalPolicy.default()
        return ApprovalManager(policy=policy)

    async def test_safe_actions_auto_approved(self, manager):
        """Safe actions should be auto-approved."""
        result = await manager.request_approval(
            command="host list",
        )

        assert result is True

    async def test_never_actions_auto_denied(self, manager):
        """Never actions should be auto-denied."""
        result = await manager.request_approval(
            command="run rm -rf /",
        )

        assert result is False

    def test_list_pending(self, manager):
        """Should list pending requests."""
        # Create a request directly without waiting
        request = ApprovalRequest(
            id="pending1",
            command="test command",
            level=ApprovalLevel.CONFIRM,
            description="Test",
            session_key="cli:default",
            channel="cli",
            user_id="test",
        )
        manager._pending[request.id] = request

        pending = manager.list_pending()

        assert len(pending) == 1
        assert pending[0].command == "test command"

    async def test_respond_approval(self, manager):
        """Should be able to respond to approval request."""
        # Create a pending request
        request = ApprovalRequest(
            id="resp1",
            command="file remove test.txt",
            level=ApprovalLevel.CONFIRM,
            description="Remove file",
            session_key="cli:default",
            channel="cli",
            user_id="test",
        )
        manager._pending[request.id] = request
        manager._futures[request.id] = asyncio.Future()

        # Respond
        success = await manager.respond(request.id, approved=True)

        assert success
        assert request.status == ApprovalStatus.APPROVED

    async def test_respond_denial(self, manager):
        """Should be able to deny approval request."""
        request = ApprovalRequest(
            id="deny1",
            command="db drop important",
            level=ApprovalLevel.DANGEROUS,
            description="Drop database",
            session_key="cli:default",
            channel="cli",
            user_id="test",
        )
        manager._pending[request.id] = request
        manager._futures[request.id] = asyncio.Future()

        success = await manager.respond(request.id, approved=False)

        assert success
        assert request.status == ApprovalStatus.DENIED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
