"""Approval manager for dangerous operations."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from navig.debug_logger import get_debug_logger

from .policies import ApprovalLevel, ApprovalPolicy, ApprovalStatus

if TYPE_CHECKING:
    from navig.gateway.audit_log import AuditLog
    from navig.gateway.server import NavigGateway

logger = get_debug_logger()


@dataclass
class ApprovalRequest:
    """A pending approval request."""

    id: str
    command: str
    level: ApprovalLevel
    description: str
    session_key: str
    channel: str
    user_id: str
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "level": self.level.value,
            "description": self.description,
            "session_key": self.session_key,
            "channel": self.channel,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "status": self.status.value,
        }


class ApprovalManager:
    """
    Manages approval flows for dangerous operations.

    Integrates with Gateway session context to route approval
    requests to the appropriate channel/user.
    """

    def __init__(
        self,
        gateway: Optional["NavigGateway"] = None,
        policy: ApprovalPolicy | None = None,
        audit_log: Optional["AuditLog"] = None,
    ):
        self.gateway = gateway
        self.policy = policy or ApprovalPolicy()
        self._audit_log: AuditLog | None = audit_log

        # Pending approvals by ID
        self._pending: dict[str, ApprovalRequest] = {}

        # Approval futures for async waiting
        self._futures: dict[str, asyncio.Future] = {}

        # Cleanup task
        self._cleanup_task: asyncio.Task | None = None

        # Event callbacks
        self._on_request_callbacks: list = []

        # Registered handlers by name (e.g., 'gateway', 'telegram')
        self._handlers: dict[str, Any] = {}

    def set_audit_log(self, audit_log: "AuditLog") -> None:
        """Wire in the audit log (called from gateway after both are initialised)."""
        self._audit_log = audit_log

    def is_audit_log_live(self) -> bool:
        """Return True when the audit log is wired and its file is writable."""
        if self._audit_log is None:
            return False
        # Probe by checking the parent dir can be created / used
        try:
            path: Path = self._audit_log._path  # type: ignore[attr-defined]
            path.parent.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False

    def set_auto_evolve(self, enabled: bool) -> None:
        """
        Toggle auto-evolve mode at runtime (called from the VS Code toggle).

        Refuses to enable when the audit log is not live — silent approvals
        without a trace are architecturally forbidden.
        """
        if enabled and not self.is_audit_log_live():
            raise RuntimeError(
                "Cannot enable auto-evolve: audit log is not live. "
                "Ensure ~/.navig/runtime/ is writable and audit_log is wired."
            )
        self.policy.auto_evolve_enabled = enabled
        logger.info("Auto-evolve %s", "ENABLED" if enabled else "DISABLED")
        if self._audit_log:
            self._audit_log.record(
                actor="navig-bridge:toggle",
                action="approval.auto_evolve.toggle",
                policy="allow",
                status="success",
                metadata={"enabled": enabled},
            )

    def register_handler(self, name: str, handler: Any) -> None:
        """Register an approval handler.

        Args:
            name: Handler identifier (e.g., 'gateway', 'telegram')
            handler: Handler instance with handle_request method
        """
        self._handlers[name] = handler
        logger.debug("Registered approval handler: %s", name)

    def unregister_handler(self, name: str) -> None:
        """Unregister an approval handler."""
        if name in self._handlers:
            del self._handlers[name]
            logger.debug("Unregistered approval handler: %s", name)

    def get_handler(self, name: str) -> Any | None:
        """Get a registered handler by name."""
        return self._handlers.get(name)

    async def start(self):
        """Start the approval manager."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("ApprovalManager started")

    async def stop(self):
        """Stop the approval manager."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        # Cancel all pending futures
        for future in self._futures.values():
            if not future.done():
                future.cancel()

        self._pending.clear()
        self._futures.clear()
        logger.info("ApprovalManager stopped")

    def on_request(self, callback: Callable):
        """Register callback for new approval requests."""
        self._on_request_callbacks.append(callback)

    async def request_approval(
        self,
        command: str,
        session_key: str = "cli:default",
        channel: str = "cli",
        user_id: str = "anonymous",
        description: str | None = None,
    ) -> bool:
        """
        Request approval for a command.

        Returns True if approved, False if denied/expired.
        Blocks until user responds or timeout.
        """
        if not self.policy.enabled:
            return True

        # Check auto-approve users
        if self.policy.is_user_auto_approved(user_id):
            logger.debug("Auto-approved (trusted user): %s", command)
            return True

        # Check auto-evolve (VS Code-side toggle) — gate: audit log must be live
        if self.policy.is_auto_evolve_allowed(command, self.is_audit_log_live()):
            logger.info("Auto-evolve approved: %s", command)
            if self._audit_log:
                self._audit_log.record(
                    actor=f"{channel}:{user_id}",
                    action="approval.auto_evolve",
                    policy="allow",
                    status="success",
                    raw_input=command,
                    metadata={"auto_evolve": True},
                )
            return True

        # Classify command
        level = self.policy.classify_command(command)

        # Auto-approve safe commands
        if level == ApprovalLevel.SAFE:
            logger.debug("Auto-approved (safe): %s", command)
            return True

        # Auto-deny never commands
        if level == ApprovalLevel.NEVER:
            logger.warning("Auto-denied (never): %s", command)
            return False

        # Create approval request
        request_id = str(uuid.uuid4())[:8]
        expires_at = datetime.now() + timedelta(seconds=self.policy.timeout_seconds)

        request = ApprovalRequest(
            id=request_id,
            command=command,
            level=level,
            description=description or f"Execute: {command}",
            session_key=session_key,
            channel=channel,
            user_id=user_id,
            expires_at=expires_at,
        )

        self._pending[request_id] = request

        # Create future for async waiting — get_running_loop() is the correct
        # API inside an async function (get_event_loop is deprecated in 3.10+).
        future = asyncio.get_running_loop().create_future()
        self._futures[request_id] = future

        # Notify callbacks
        for callback in self._on_request_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(request)
                else:
                    callback(request)
            except Exception as e:
                logger.error("Approval callback error: %s", e)

        try:
            # Wait for response or timeout
            result = await asyncio.wait_for(future, timeout=self.policy.timeout_seconds)
            return result

        except asyncio.TimeoutError:
            # Timeout - apply default action
            request.status = ApprovalStatus.EXPIRED
            default_approve = self.policy.default_action == "approve"

            if level == ApprovalLevel.DANGEROUS:
                # Dangerous commands default to deny on timeout
                default_approve = False

            logger.info(
                f"Approval timeout for {request_id}: {'approved' if default_approve else 'denied'}"
            )
            return default_approve

        except asyncio.CancelledError:
            request.status = ApprovalStatus.DENIED
            return False

        finally:
            # Cleanup
            self._pending.pop(request_id, None)
            self._futures.pop(request_id, None)

    async def respond(self, request_id: str, approved: bool) -> bool:
        """
        Respond to an approval request.

        Returns True if request was found and responded to.
        """
        request = self._pending.get(request_id)
        if not request:
            logger.warning("Approval request not found: %s", request_id)
            return False

        request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED

        # Resolve the waiting future
        future = self._futures.get(request_id)
        if future and not future.done():
            future.set_result(approved)

        logger.info("Approval %s: %s", request_id, 'approved' if approved else 'denied')
        return True

    def get_pending(self, channel: str | None = None, user_id: str | None = None) -> list:
        """Get pending approval requests, optionally filtered."""
        requests = list(self._pending.values())

        if channel:
            requests = [r for r in requests if r.channel == channel]

        if user_id:
            requests = [r for r in requests if r.user_id == user_id]

        return [r.to_dict() for r in requests]

    def list_pending(self) -> list:
        """List pending approval requests. Alias for testing compatibility."""
        return list(self._pending.values())

    def get_request(self, request_id: str) -> dict | None:
        """Get a specific request by ID."""
        request = self._pending.get(request_id)
        return request.to_dict() if request else None

    def format_approval_message(self, request: ApprovalRequest) -> str:
        """Format approval request as user-friendly message."""
        level_emoji = {
            ApprovalLevel.CONFIRM: "⚠️",
            ApprovalLevel.DANGEROUS: "🚨",
        }
        emoji = level_emoji.get(request.level, "❓")

        time_remaining = ""
        if request.expires_at:
            remaining = (request.expires_at - datetime.now()).total_seconds()
            if remaining > 0:
                time_remaining = f" (expires in {int(remaining)}s)"

        return (
            f"{emoji} **Approval Required**{time_remaining}\n\n"
            f"**Command:** `{request.command}`\n"
            f"**Level:** {request.level.value}\n"
            f"**ID:** `{request.id}`\n\n"
            f"Reply with `/approve {request.id}` or `/deny {request.id}`"
        )

    async def _cleanup_loop(self):
        """Periodically clean up expired requests."""
        while True:
            try:
                await asyncio.sleep(30)
                now = datetime.now()

                expired_ids = [
                    req_id
                    for req_id, req in self._pending.items()
                    if req.expires_at and req.expires_at < now
                ]

                for req_id in expired_ids:
                    request = self._pending.pop(req_id, None)
                    if request:
                        request.status = ApprovalStatus.EXPIRED

                    future = self._futures.pop(req_id, None)
                    if future and not future.done():
                        future.set_result(False)  # Deny on expiry

                if expired_ids:
                    logger.debug("Cleaned up %s expired approval requests", len(expired_ids))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Approval cleanup error: %s", e)
