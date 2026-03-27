"""Channel-specific approval handlers."""

import asyncio
from typing import TYPE_CHECKING, Any

from navig.debug_logger import get_debug_logger

from .manager import ApprovalRequest

if TYPE_CHECKING:
    from .manager import ApprovalManager

logger = get_debug_logger()


class TelegramApprovalHandler:
    """
    Telegram-specific approval handler.

    Uses inline keyboards for approval buttons.
    """

    def __init__(self, manager: "ApprovalManager", bot: Any = None):
        self.manager = manager
        self.bot = bot

        # Register for approval requests
        self.manager.on_request(self.on_approval_request)

    async def on_approval_request(self, request: ApprovalRequest):
        """Handle new approval request - send Telegram message with buttons."""
        if request.channel != "telegram" or not self.bot:
            return

        try:
            # Import here to avoid circular deps
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Approve", callback_data=f"approve:{request.id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Deny", callback_data=f"deny:{request.id}"
                        ),
                    ]
                ]
            )

            level_emoji = {"confirm": "⚠️", "dangerous": "🚨"}.get(
                request.level.value, "❓"
            )

            message = (
                f"{level_emoji} **Approval Required**\n\n"
                f"**Command:** `{request.command}`\n"
                f"**Level:** {request.level.value}\n"
                f"**Expires:** {request.expires_at.strftime('%H:%M:%S') if request.expires_at else 'Never'}"
            )

            # Send to user
            chat_id = request.user_id
            await self.bot.send_message(
                chat_id=int(chat_id),
                text=message,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

        except Exception as e:
            logger.error(f"Failed to send Telegram approval request: {e}")

    async def handle_callback(
        self, callback_data: str, user_id: str
    ) -> tuple[bool, str]:
        """
        Handle Telegram callback button press.

        Returns (success, message).
        """
        if ":" not in callback_data:
            return False, "Invalid callback data"

        action, request_id = callback_data.split(":", 1)

        if action not in ("approve", "deny"):
            return False, "Unknown action"

        approved = action == "approve"
        success = await self.manager.respond(request_id, approved)

        if success:
            result = "✅ Approved" if approved else "❌ Denied"
            return True, f"{result}: `{request_id}`"
        else:
            return False, "⚠️ Request not found or expired"


class CLIApprovalHandler:
    """
    CLI approval handler.

    Uses terminal prompts for interactive approval.
    """

    def __init__(self, manager: "ApprovalManager"):
        self.manager = manager

    async def prompt_approval(self, request: ApprovalRequest) -> bool:
        """
        Prompt user for approval in terminal.

        Returns True if approved.
        """
        from rich.console import Console
        from rich.prompt import Confirm

        console = Console()

        level_emoji = {"confirm": "⚠️", "dangerous": "🚨"}.get(request.level.value, "❓")

        console.print(f"\n{level_emoji} [bold]Approval Required[/bold]")
        console.print(f"  Command: [cyan]{request.command}[/cyan]")
        console.print(f"  Level: [yellow]{request.level.value}[/yellow]")
        console.print(f"  ID: [dim]{request.id}[/dim]")

        try:
            approved = Confirm.ask("Approve this command?", default=False)
            await self.manager.respond(request.id, approved)
            return approved
        except KeyboardInterrupt:
            await self.manager.respond(request.id, False)
            return False


class GatewayApprovalHandler:
    """
    Gateway API approval handler.

    Exposes approval via REST endpoints.
    """

    def __init__(self, manager: "ApprovalManager"):
        self.manager = manager

    async def handle_request(self, data: dict) -> dict:
        """Handle approval request via API."""
        command = data.get("command")
        session_key = data.get("session_key", "api:default")
        channel = data.get("channel", "api")
        user_id = data.get("user_id", "anonymous")
        description = data.get("description")

        if not command:
            return {"error": "Missing 'command' field"}

        # Non-blocking: create request and return immediately
        # Caller must poll or wait for WebSocket notification
        if data.get("async", False):
            request_id = await self._create_async_request(
                command, session_key, channel, user_id, description
            )
            return {"request_id": request_id, "status": "pending"}

        # Blocking: wait for approval
        approved = await self.manager.request_approval(
            command=command,
            session_key=session_key,
            channel=channel,
            user_id=user_id,
            description=description,
        )

        return {"approved": approved, "command": command}

    async def _create_async_request(
        self,
        command: str,
        session_key: str,
        channel: str,
        user_id: str,
        description: str | None,
    ) -> str:
        """Create an async approval request that doesn't block."""
        import uuid
        from datetime import datetime, timedelta

        request_id = str(uuid.uuid4())[:8]
        expires_at = datetime.now() + timedelta(
            seconds=self.manager.policy.timeout_seconds
        )

        from .manager import ApprovalRequest

        level = self.manager.policy.classify_command(command)

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

        self.manager._pending[request_id] = request

        # Notify callbacks
        for callback in self.manager._on_request_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(request)
                else:
                    callback(request)
            except Exception as e:
                logger.error(f"Approval callback error: {e}")

        return request_id

    async def handle_respond(self, request_id: str, approved: bool) -> dict:
        """Handle approval response via API."""
        success = await self.manager.respond(request_id, approved)

        if success:
            return {"ok": True, "approved": approved, "request_id": request_id}
        else:
            return {"error": "Request not found or expired"}

    def handle_list_pending(self, channel: str | None = None) -> dict:
        """List pending approval requests."""
        pending = self.manager.get_pending(channel=channel)
        return {"pending": pending, "count": len(pending)}
