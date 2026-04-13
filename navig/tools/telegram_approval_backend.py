"""
navig.tools.telegram_approval_backend — Telegram inline-keyboard approval backend.

Provides :class:`TelegramApprovalBackend`, a pluggable backend for
:class:`~navig.tools.approval.ApprovalGate` that sends approval requests
as Telegram messages with inline keyboards and waits for user response.

Usage::

    from navig.tools.approval import get_approval_gate
    from navig.tools.telegram_approval_backend import TelegramApprovalBackend

    backend = TelegramApprovalBackend(chat_id="12345")
    get_approval_gate().backend = backend

Architecture::

    Agent wants to run dangerous command
      → ApprovalGate.check() → needs_approval() == True
      → Routes to TelegramApprovalBackend.__call__()
      → Sends Telegram message with inline keyboard: [✅ Approve] [❌ Deny]
      → Waits for callback (configurable timeout, default 120s)
      → Callback received → ApprovalDecision.APPROVED / DENIED
      → Timeout → risk-based: low/medium auto-approve, high/critical auto-deny
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from navig.tools.approval import ApprovalDecision, ApprovalRequest

logger = logging.getLogger(__name__)

__all__ = [
    "TelegramApprovalBackend",
    "ApprovalMessage",
]


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT: int = 120  # seconds

RISK_EMOJIS: dict[str, str] = {
    "safe": "🟢",
    "moderate": "🟡",
    "dangerous": "🟠",
    "critical": "🔴",
}

# Callback data prefix used in inline keyboard buttons
CALLBACK_PREFIX = "approval:"

# Risk levels that auto-approve on timeout (conservative default)
AUTO_APPROVE_ON_TIMEOUT: frozenset[str] = frozenset({"safe", "moderate"})


# ─────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────


@dataclass
class ApprovalMessage:
    """Tracks a pending approval request sent to Telegram.

    Attributes:
        request_id:   Unique identifier for this approval request.
        request:      The original :class:`ApprovalRequest` from the gate.
        future:       Asyncio future resolved when the user responds.
        message_id:   Telegram message ID (set after send).
        chat_id:      Target Telegram chat ID.
        created_at:   Unix timestamp when the request was created.
        resolved_at:  Unix timestamp when resolved (or ``None``).
        resolved_by:  How the request was resolved: ``"user"``, ``"timeout"``, or ``""``.
    """

    request_id: str
    request: ApprovalRequest
    future: asyncio.Future[bool] = field(repr=False, default=None)  # type: ignore[assignment]
    message_id: int | None = None
    chat_id: str = ""
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    resolved_by: str = ""


# ─────────────────────────────────────────────────────────────
# Message formatting
# ─────────────────────────────────────────────────────────────


def format_approval_message(req: ApprovalRequest, request_id: str) -> str:
    """Build the Telegram message text for an approval request.

    Args:
        req:        The approval request payload.
        request_id: Unique identifier for the request.

    Returns:
        Markdown-formatted message string.
    """
    emoji = RISK_EMOJIS.get(req.safety_level, "🟡")

    lines = [
        f"{emoji} <b>Approval Required</b>",
        "",
        f"<b>Tool:</b> <code>{req.tool_name}</code>",
        f"<b>Risk:</b> {req.safety_level.upper()}",
    ]

    if req.reason:
        lines.append(f"<b>Reason:</b> {req.reason}")

    if req.parameters:
        details = _format_details(req.parameters)
        if details:
            lines.append("")
            lines.append("<b>Details:</b>")
            lines.extend(f"  • <code>{k}</code>: {v}" for k, v in details.items())

    lines.append("")
    lines.append(f"<i>Request ID: {request_id}</i>")

    return "\n".join(lines)


def _format_details(params: dict[str, Any], max_value_len: int = 200) -> dict[str, str]:
    """Format tool parameters for display, truncating long values."""
    result: dict[str, str] = {}
    for k, v in params.items():
        s = str(v)
        if len(s) > max_value_len:
            s = s[:max_value_len] + "…"
        result[k] = s
    return result


def build_inline_keyboard(request_id: str) -> dict[str, Any]:
    """Build the Telegram inline keyboard markup for approve/deny.

    Args:
        request_id: Unique identifier embedded in callback data.

    Returns:
        A dict suitable for Telegram's ``reply_markup`` parameter.
    """
    return {
        "inline_keyboard": [[
            {
                "text": "✅ Approve",
                "callback_data": f"{CALLBACK_PREFIX}approve:{request_id}",
            },
            {
                "text": "❌ Deny",
                "callback_data": f"{CALLBACK_PREFIX}deny:{request_id}",
            },
        ]]
    }


def parse_callback_data(callback_data: str) -> tuple[str, str] | None:
    """Parse a Telegram callback_data string into (action, request_id).

    Expected format: ``approval:approve:<request_id>`` or
    ``approval:deny:<request_id>``.

    Returns:
        A tuple ``(action, request_id)`` where action is ``"approve"`` or
        ``"deny"``, or ``None`` if the data doesn't match.
    """
    if not callback_data.startswith(CALLBACK_PREFIX):
        return None
    rest = callback_data[len(CALLBACK_PREFIX):]
    parts = rest.split(":", 1)
    if len(parts) != 2:
        return None
    action, request_id = parts
    if action not in ("approve", "deny"):
        return None
    return action, request_id


# ─────────────────────────────────────────────────────────────
# TelegramApprovalBackend
# ─────────────────────────────────────────────────────────────


class TelegramApprovalBackend:
    """Approval backend that sends inline-keyboard messages via Telegram.

    This class is a callable that conforms to the
    :data:`~navig.tools.approval.ApprovalBackend` protocol and can be
    injected into :class:`~navig.tools.approval.ApprovalGate`.

    Args:
        chat_id:              Target Telegram chat ID for approval messages.
        timeout:              Seconds to wait for user response before applying
                              the timeout policy (default 120).
        send_fn:              Async callable ``(chat_id, text, reply_markup) -> dict``
                              that sends a Telegram message. If ``None``, uses
                              the built-in HTTP sender (requires ``bot_token``).
        edit_fn:              Async callable ``(chat_id, message_id, text) -> None``
                              that edits an existing message. Optional.
        bot_token:            Telegram bot token (used only with the built-in sender).
        auto_approve_levels:  Risk levels that auto-approve on timeout.

    Example::

        backend = TelegramApprovalBackend(chat_id="12345", bot_token="BOT:TOKEN")
        get_approval_gate().backend = backend

        # Or with a custom sender (e.g. from an existing bot instance):
        async def my_send(chat_id, text, reply_markup):
            return await bot.send_message(chat_id, text, reply_markup=reply_markup)

        backend = TelegramApprovalBackend(chat_id="12345", send_fn=my_send)
    """

    def __init__(
        self,
        chat_id: str,
        timeout: int = DEFAULT_TIMEOUT,
        send_fn: Any | None = None,
        edit_fn: Any | None = None,
        bot_token: str = "",
        auto_approve_levels: frozenset[str] | None = None,
    ) -> None:
        self.chat_id = chat_id
        self.timeout = timeout
        self._send_fn = send_fn
        self._edit_fn = edit_fn
        self._bot_token = bot_token
        self._auto_approve_levels = auto_approve_levels or AUTO_APPROVE_ON_TIMEOUT

        # Pending requests keyed by request_id
        self._pending: dict[str, ApprovalMessage] = {}

    # ── ApprovalBackend protocol ────────────────────────────

    async def __call__(self, req: ApprovalRequest) -> ApprovalDecision:
        """Send an approval request to Telegram and wait for response.

        This method conforms to the ``ApprovalBackend`` callable protocol.

        Args:
            req: The approval request from the gate.

        Returns:
            :class:`ApprovalDecision` — APPROVED, DENIED, or TIMEOUT.
        """
        request_id = uuid4().hex[:12]

        # Build message and keyboard
        text = format_approval_message(req, request_id)
        keyboard = build_inline_keyboard(request_id)

        # Create a future for the callback response
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        msg = ApprovalMessage(
            request_id=request_id,
            request=req,
            future=future,
            chat_id=self.chat_id,
        )
        self._pending[request_id] = msg

        # Send the message
        try:
            send_result = await self._send(self.chat_id, text, keyboard)
            if isinstance(send_result, dict):
                # Extract message_id from Telegram API JSON response
                result_data = send_result.get("result", send_result)
                msg.message_id = result_data.get("message_id")
        except Exception as exc:
            logger.error("telegram_approval: failed to send message: %s", exc)
            self._pending.pop(request_id, None)
            return ApprovalDecision.DENIED

        # Wait for callback with timeout
        try:
            approved = await asyncio.wait_for(future, timeout=self.timeout)
            msg.resolved_at = time.time()
            msg.resolved_by = "user"
            decision = ApprovalDecision.APPROVED if approved else ApprovalDecision.DENIED
        except asyncio.TimeoutError:
            msg.resolved_at = time.time()
            msg.resolved_by = "timeout"
            approved = self._timeout_policy(req.safety_level)
            decision = ApprovalDecision.APPROVED if approved else ApprovalDecision.TIMEOUT

            # Update the message to show timeout result
            suffix = "Auto-approved ✅" if approved else "Auto-denied ⏰"
            await self._try_edit(
                msg.chat_id,
                msg.message_id,
                text + f"\n\n⏰ <b>Timed out</b> → {suffix}",
            )

        self._pending.pop(request_id, None)

        logger.info(
            "telegram_approval: tool='%s' request_id=%s decision=%s resolved_by=%s",
            req.tool_name,
            request_id,
            decision.value,
            msg.resolved_by,
        )
        return decision

    # ── Callback handling ───────────────────────────────────

    def handle_callback(self, callback_data: str) -> bool:
        """Handle a Telegram callback query from an inline keyboard button.

        This should be called by the Telegram gateway when a callback
        query is received with data matching the ``approval:`` prefix.

        Args:
            callback_data: The ``callback_data`` string from the button press.

        Returns:
            ``True`` if the callback was handled, ``False`` otherwise.
        """
        parsed = parse_callback_data(callback_data)
        if parsed is None:
            return False

        action, request_id = parsed
        msg = self._pending.get(request_id)
        if msg is None:
            logger.debug("telegram_approval: no pending request for id=%s", request_id)
            return False

        if msg.future.done():
            logger.debug("telegram_approval: future already resolved for id=%s", request_id)
            return False

        approved = action == "approve"
        msg.future.set_result(approved)

        logger.info(
            "telegram_approval: callback received — request_id=%s action=%s",
            request_id,
            action,
        )
        return True

    # ── Query ───────────────────────────────────────────────

    @property
    def pending_count(self) -> int:
        """Number of currently pending approval requests."""
        return len(self._pending)

    def get_pending(self, request_id: str) -> ApprovalMessage | None:
        """Return a pending approval message by ID, or ``None``."""
        return self._pending.get(request_id)

    # ── Timeout policy ──────────────────────────────────────

    def _timeout_policy(self, safety_level: str) -> bool:
        """Determine whether to auto-approve on timeout.

        Args:
            safety_level: The risk level string from the request.

        Returns:
            ``True`` to auto-approve, ``False`` to deny.
        """
        return safety_level in self._auto_approve_levels

    # ── Transport ───────────────────────────────────────────

    async def _send(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a message via the configured transport.

        Uses ``send_fn`` if provided, otherwise falls back to HTTP API.
        """
        if self._send_fn is not None:
            return await self._send_fn(chat_id, text, reply_markup)
        return await self._send_http(chat_id, text, reply_markup)

    async def _send_http(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any],
    ) -> dict[str, Any]:
        """Send via Telegram Bot API HTTP endpoint."""
        if not self._bot_token:
            raise RuntimeError(
                "TelegramApprovalBackend: no bot_token and no send_fn — "
                "cannot send approval message"
            )
        import aiohttp

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(reply_markup),
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                return await resp.json()

    async def _try_edit(
        self,
        chat_id: str,
        message_id: int | None,
        text: str,
    ) -> None:
        """Best-effort edit of an existing Telegram message."""
        if message_id is None:
            return
        try:
            if self._edit_fn is not None:
                await self._edit_fn(chat_id, message_id, text)
            elif self._bot_token:
                await self._edit_http(chat_id, message_id, text)
        except Exception as exc:
            logger.debug("telegram_approval: failed to edit message: %s", exc)

    async def _edit_http(
        self,
        chat_id: str,
        message_id: int,
        text: str,
    ) -> None:
        """Edit via Telegram Bot API HTTP endpoint."""
        import aiohttp

        url = f"https://api.telegram.org/bot{self._bot_token}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload)
