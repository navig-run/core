"""
NAVIG Auth Guard — Access control for Telegram channel.

Simple allowlist-based guard that checks user_id / username against
configured allowed sets. When a user is NOT authorized, delegates
to DecoyResponder for playful non-actionable responses.

Designed to be imported by telegram.py and called at the permission
check point before any real processing.
"""

import logging
from typing import Optional, Set

logger = logging.getLogger(__name__)


class AuthGuard:
    """
    Stateless access-control gate for incoming Telegram messages.

    Usage:
        guard = AuthGuard(allowed_users={123, 456}, allowed_groups={-789})
        if guard.is_authorized(user_id, chat_id, is_group):
            # proceed with real processing
        else:
            # fire decoy response
    """

    def __init__(
        self,
        allowed_users: Optional[Set[int]] = None,
        allowed_groups: Optional[Set[int]] = None,
    ):
        self.allowed_users: Set[int] = allowed_users or set()
        self.allowed_groups: Set[int] = allowed_groups or set()

    def is_authorized(
        self,
        user_id: int,
        chat_id: int,
        is_group: bool = False,
    ) -> bool:
        """
        Return True if user/chat is allowed to use the bot.

        Rules:
        - If allowed_users is empty → everyone is authorized (open mode)
        - If user_id is in allowed_users → authorized
        - If is_group and chat_id is in allowed_groups → authorized
        - Otherwise → not authorized
        """
        if not self.allowed_users:
            return True  # open mode

        if user_id in self.allowed_users:
            return True

        if is_group and chat_id in self.allowed_groups:
            return True

        logger.info(
            "Auth denied: user_id=%s chat_id=%s is_group=%s",
            user_id,
            chat_id,
            is_group,
        )
        return False
