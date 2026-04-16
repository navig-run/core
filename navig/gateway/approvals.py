"""
navig.gateway.approvals
~~~~~~~~~~~~~~~~~~~~~~~
Telegram-native async approval flow for destructive remote operations.

Usage (from a command handler)::

    from navig.gateway.approvals import ApprovalStore, APPROVAL_PREFIX

    store = ApprovalStore()  # typically one shared instance per gateway

    token = await store.request(
        description="DROP TABLE users",
        timeout_s=120,
    )
    # ... tell the requester session to wait ...
    # In the callback handler, look up by token:
    result = await store.wait(token)  # "approved" | "rejected" | "timed_out"

Inline-keyboard callbacks use the prefix ``_appr_`` followed by the action
and UUID: ``_appr_ok_<uuid>`` or ``_appr_no_<uuid>``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("navig.gateway.approvals")

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

APPROVAL_PREFIX = "_appr_"
_ACTION_OK = "ok"
_ACTION_NO = "no"
_DEFAULT_TIMEOUT_S = 120
_DEFAULT_ADMIN_CHAT_ID: str | None = None


# ─────────────────────────────────────────────────────────────
# State model
# ─────────────────────────────────────────────────────────────


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass
class PendingApproval:
    """A single approval request."""

    id: str
    description: str
    timeout_s: int
    state: ApprovalState = ApprovalState.PENDING
    # Internal event signalled when the state changes.
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def resolve(self, state: ApprovalState) -> None:
        """Set the terminal state and signal any waiter."""
        if self.state is not ApprovalState.PENDING:
            logger.debug("Approval %s already resolved (%s); ignoring %s", self.id, self.state, state)
            return
        self.state = state
        self._event.set()

    def is_terminal(self) -> bool:
        return self.state is not ApprovalState.PENDING


# ─────────────────────────────────────────────────────────────
# Store
# ─────────────────────────────────────────────────────────────


class ApprovalStore:
    """
    In-process registry of in-flight approval requests.

    One shared instance per gateway server is the intended pattern.
    All public methods are safe to call from any coroutine or thread.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    def create(self, description: str, timeout_s: int = _DEFAULT_TIMEOUT_S) -> PendingApproval:
        """
        Create and register a new approval request.

        Returns the ``PendingApproval`` immediately (state = PENDING).
        Callers should then send the inline keyboard to the admin and call
        :meth:`wait` to block until the admin responds.
        """
        approval_id = str(uuid.uuid4())
        entry = PendingApproval(id=approval_id, description=description, timeout_s=timeout_s)
        self._pending[approval_id] = entry
        logger.info("Approval %s created: %r (timeout=%ss)", approval_id, description, timeout_s)
        return entry

    async def wait(self, approval_id: str) -> ApprovalState:
        """
        Block until the approval is resolved (approved / rejected / timed out).

        If the approval_id is not found, returns ``CANCELLED``.
        """
        entry = self._pending.get(approval_id)
        if entry is None:
            logger.warning("wait() called for unknown approval %s", approval_id)
            return ApprovalState.CANCELLED

        try:
            await asyncio.wait_for(entry._event.wait(), timeout=entry.timeout_s)
        except asyncio.TimeoutError:
            entry.resolve(ApprovalState.TIMED_OUT)

        self._pending.pop(approval_id, None)
        logger.info("Approval %s settled: %s", approval_id, entry.state)
        return entry.state

    def approve(self, approval_id: str) -> bool:
        """
        Approve a pending request.

        Returns ``True`` if the request was found and resolved, ``False``
        if it was unknown or already terminal.
        """
        return self._resolve(approval_id, ApprovalState.APPROVED)

    def reject(self, approval_id: str) -> bool:
        """Reject a pending request."""
        return self._resolve(approval_id, ApprovalState.REJECTED)

    def cancel(self, approval_id: str) -> bool:
        """Cancel a pending request (e.g. session closed)."""
        return self._resolve(approval_id, ApprovalState.CANCELLED)

    def _resolve(self, approval_id: str, state: ApprovalState) -> bool:
        entry = self._pending.get(approval_id)
        if entry is None or entry.is_terminal():
            return False
        entry.resolve(state)
        return True

    # ------------------------------------------------------------------
    # Callback data helpers
    # ------------------------------------------------------------------

    @staticmethod
    def encode_callback(action: str, approval_id: str) -> str:
        """Build the callback_data string to embed in an inline button."""
        return f"{APPROVAL_PREFIX}{action}_{approval_id}"

    @staticmethod
    def decode_callback(data: str) -> tuple[str, str] | None:
        """
        Parse a callback_data string.

        Returns ``(action, approval_id)`` or ``None`` if not an approval callback.

        >>> ApprovalStore.decode_callback("_appr_ok_abc-123")
        ('ok', 'abc-123')
        >>> ApprovalStore.decode_callback("something_else") is None
        True
        """
        if not data.startswith(APPROVAL_PREFIX):
            return None
        rest = data[len(APPROVAL_PREFIX):]
        # rest = "ok_<uuid>" or "no_<uuid>"
        for action in (_ACTION_OK, _ACTION_NO):
            prefix = f"{action}_"
            if rest.startswith(prefix):
                return action, rest[len(prefix):]
        return None

    def handle_callback(self, data: str) -> bool:
        """
        Dispatch an inline-keyboard callback to the relevant approval.

        Returns ``True`` if the callback was recognised and processed.
        """
        parsed = self.decode_callback(data)
        if parsed is None:
            return False
        action, approval_id = parsed
        if action == _ACTION_OK:
            resolved = self.approve(approval_id)
        else:
            resolved = self.reject(approval_id)
        if not resolved:
            logger.warning("handle_callback: approval %s not found or already resolved", approval_id)
        return True

    # ------------------------------------------------------------------
    # Telegram message builder (no import of bot SDK — returns plain dicts)
    # ------------------------------------------------------------------

    def build_message(self, entry: PendingApproval) -> dict:
        """
        Return a plain dict with ``text`` and ``reply_markup`` keys suitable
        for passing to any Telegram send-message helper.

        Keeps this module free of bot SDK imports so it can be tested without
        a live bot.
        """
        ok_data = self.encode_callback(_ACTION_OK, entry.id)
        no_data = self.encode_callback(_ACTION_NO, entry.id)
        text = (
            f"⚠️ *Approval Required*\n\n"
            f"`{entry.description[:400]}`\n\n"
            f"Timeout: {entry.timeout_s}s"
        )
        reply_markup = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": ok_data},
                {"text": "❌ Reject", "callback_data": no_data},
            ]]
        }
        return {"text": text, "reply_markup": reply_markup, "parse_mode": "Markdown"}

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def pending_count(self) -> int:
        return sum(1 for e in self._pending.values() if not e.is_terminal())

    def all_ids(self) -> list[str]:
        return list(self._pending.keys())
