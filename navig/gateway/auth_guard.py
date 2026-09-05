"""
NAVIG Auth Guard — allowlist helper. **NOT the live authorization gate.**

⚠ Do not wire this into the Telegram channel. The live gate is
``TelegramChannel._is_user_authorized`` (``gateway/channels/telegram.py``),
which this module does NOT feed — it has no importer anywhere in production.
This docstring used to say "Designed to be imported by telegram.py and called
at the permission check point", which is an instruction to replace a working
check with this one.

That swap would WEAKEN authorization, because the two disagree on the empty
case in opposite directions:

* here            — empty ``allowed_users`` ⇒ **everyone is authorized**
* the live gate   — ``require_auth`` on with an empty list ⇒ **deny all**
                    (``require_auth`` off is the explicit open mode)

Fail-open versus fail-closed on the same input. Keep the live gate; if this
helper is ever adopted, port the live policy into it first.

Simple allowlist-based check of user_id / chat_id against configured sets.
Callers that deny are expected to answer via ``gateway/decoy_responder.py``
(which telegram.py already uses directly).
"""

import logging

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
        allowed_users: set[int] | None = None,
        allowed_groups: set[int] | None = None,
    ):
        self.allowed_users: set[int] = allowed_users or set()
        self.allowed_groups: set[int] = allowed_groups or set()

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
