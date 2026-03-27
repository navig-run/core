"""
Telegram Session Management

Per-user and per-group session isolation with:
- Reply tracking and threading
- Mention gating for groups
- Automatic session cleanup
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionMessage:
    """A message in session history."""

    role: str  # 'user' or 'assistant'
    content: str
    timestamp: str
    message_id: int | None = None
    reply_to: int | None = None


@dataclass
class TelegramSession:
    """
    Session state for a Telegram user or group.

    Session key format:
    - DM: telegram:user:<user_id>
    - Group: telegram:group:<group_id>
    """

    session_key: str
    user_id: int
    chat_id: int
    is_group: bool = False
    username: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_active: str = field(default_factory=lambda: datetime.now().isoformat())
    message_count: int = 0
    messages: list[SessionMessage] = field(default_factory=list)
    context_summary: str = ""
    reply_chain: list[int] = field(default_factory=list)
    autoheal_enabled: bool = False
    autoheal_hive_enabled: bool = False
    heal_history: list[dict] = field(default_factory=list)

    def add_message(
        self,
        role: str,
        content: str,
        message_id: int | None = None,
        reply_to: int | None = None,
    ):
        """Add a message to session history."""
        self.messages.append(
            SessionMessage(
                role=role,
                content=content,
                timestamp=datetime.now().isoformat(),
                message_id=message_id,
                reply_to=reply_to,
            )
        )
        self.message_count += 1
        self.last_active = datetime.now().isoformat()

        # Track reply chain
        if message_id:
            self.reply_chain.append(message_id)
            # Keep only last 50 message IDs
            self.reply_chain = self.reply_chain[-50:]

    def get_context_messages(self, limit: int = 20) -> list[dict[str, str]]:
        """Get recent messages for AI context."""
        recent = self.messages[-limit:]
        return [{"role": m.role, "content": m.content} for m in recent]

    def is_in_reply_chain(self, message_id: int) -> bool:
        """Check if a message ID is part of this session's reply chain."""
        return message_id in self.reply_chain

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "session_key": self.session_key,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "is_group": self.is_group,
            "username": self.username,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "message_count": self.message_count,
            "messages": [asdict(m) for m in self.messages],
            "context_summary": self.context_summary,
            "reply_chain": self.reply_chain,
            "autoheal_enabled": self.autoheal_enabled,
            "autoheal_hive_enabled": self.autoheal_hive_enabled,
            "heal_history": self.heal_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TelegramSession":
        """Create session from dictionary."""
        messages = [SessionMessage(**m) for m in data.get("messages", [])]
        return cls(
            session_key=data["session_key"],
            user_id=data["user_id"],
            chat_id=data["chat_id"],
            is_group=data.get("is_group", False),
            username=data.get("username", ""),
            created_at=data.get("created_at", datetime.now().isoformat()),
            last_active=data.get("last_active", datetime.now().isoformat()),
            message_count=data.get("message_count", 0),
            messages=messages,
            context_summary=data.get("context_summary", ""),
            reply_chain=data.get("reply_chain", []),
            autoheal_enabled=data.get("autoheal_enabled", False),
            autoheal_hive_enabled=data.get("autoheal_hive_enabled", False),
            heal_history=data.get("heal_history", []),
        )


class SessionManager:
    """
    Manages Telegram sessions with persistence.

    Features:
    - Per-user session isolation
    - Per-group session isolation
    - Automatic session cleanup
    - File-based persistence
    """

    def __init__(
        self,
        storage_dir: Path | None = None,
        max_messages: int = 100,
        session_timeout_days: int = 7,
    ):
        self.storage_dir = storage_dir or (Path.home() / ".navig" / "telegram_sessions")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.max_messages = max_messages
        self.session_timeout_days = session_timeout_days

        self._sessions: dict[str, TelegramSession] = {}
        self._load_sessions()

    def _get_session_key(self, chat_id: int, user_id: int, is_group: bool) -> str:
        """Generate session key."""
        if is_group:
            return f"telegram:group:{chat_id}"
        else:
            return f"telegram:user:{user_id}"

    def _get_session_file(self, session_key: str) -> Path:
        """Get file path for session."""
        # Sanitize key for filename
        safe_key = session_key.replace(":", "_")
        return self.storage_dir / f"{safe_key}.json"

    def _load_sessions(self):
        """Load all sessions from disk."""
        for session_file in self.storage_dir.glob("*.json"):
            try:
                with open(session_file, encoding="utf-8") as f:
                    data = json.load(f)
                session = TelegramSession.from_dict(data)
                self._sessions[session.session_key] = session
            except Exception as e:
                logger.error(f"Failed to load session {session_file}: {e}")

    def _save_session(self, session: TelegramSession):
        """Save session to disk."""
        session_file = self._get_session_file(session.session_key)
        try:
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save session: {e}")

    def get_or_create_session(
        self, chat_id: int, user_id: int, is_group: bool = False, username: str = ""
    ) -> TelegramSession:
        """
        Get existing session or create new one.
        """
        session_key = self._get_session_key(chat_id, user_id, is_group)

        if session_key not in self._sessions:
            self._sessions[session_key] = TelegramSession(
                session_key=session_key,
                user_id=user_id,
                chat_id=chat_id,
                is_group=is_group,
                username=username,
            )

        return self._sessions[session_key]

    def add_user_message(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        message_id: int | None = None,
        reply_to: int | None = None,
        is_group: bool = False,
        username: str = "",
    ) -> TelegramSession:
        """Add a user message to session."""
        session = self.get_or_create_session(chat_id, user_id, is_group, username)
        session.add_message("user", text, message_id, reply_to)

        # Trim old messages
        if len(session.messages) > self.max_messages:
            session.messages = session.messages[-self.max_messages :]

        self._save_session(session)
        return session

    def add_assistant_message(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        message_id: int | None = None,
        is_group: bool = False,
    ) -> TelegramSession:
        """Add an assistant response to session."""
        session = self.get_or_create_session(chat_id, user_id, is_group)
        session.add_message("assistant", text, message_id)
        self._save_session(session)
        return session

    def get_session(
        self, chat_id: int, user_id: int, is_group: bool = False
    ) -> TelegramSession | None:
        """Get session if it exists."""
        session_key = self._get_session_key(chat_id, user_id, is_group)
        return self._sessions.get(session_key)

    def clear_session(self, chat_id: int, user_id: int, is_group: bool = False):
        """Clear a session's message history."""
        session_key = self._get_session_key(chat_id, user_id, is_group)

        if session_key in self._sessions:
            session = self._sessions[session_key]
            session.messages = []
            session.message_count = 0
            session.reply_chain = []
            session.context_summary = ""
            self._save_session(session)

    def delete_session(self, session_key: str):
        """Delete a session completely."""
        if session_key in self._sessions:
            del self._sessions[session_key]

        session_file = self._get_session_file(session_key)
        if session_file.exists():
            session_file.unlink()

    def list_sessions(self) -> list[TelegramSession]:
        """List all sessions."""
        return list(self._sessions.values())

    def prune_inactive(self) -> int:
        """
        Remove sessions inactive for longer than timeout.

        Returns number of sessions removed.
        """
        cutoff = datetime.now() - timedelta(days=self.session_timeout_days)
        removed = 0

        to_remove = []
        for key, session in self._sessions.items():
            last_active = datetime.fromisoformat(session.last_active)
            if last_active < cutoff:
                to_remove.append(key)

        for key in to_remove:
            self.delete_session(key)
            removed += 1

        return removed


class MentionGate:
    """
    Handles mention-based activation in groups.

    Modes:
    - "mention": Only respond when @mentioned or replied to
    - "always": Respond to all messages
    - "admin_only": Only respond to admin users
    """

    def __init__(
        self,
        bot_username: str,
        mode: str = "mention",
        admin_users: list[int] | None = None,
    ):
        self.bot_username = bot_username.lower().lstrip("@")
        self.mode = mode
        self.admin_users = set(admin_users or [])

    def should_respond(
        self,
        text: str,
        user_id: int,
        is_group: bool,
        is_reply_to_bot: bool = False,
        session: TelegramSession | None = None,
        reply_to_message_id: int | None = None,
    ) -> bool:
        """
        Determine if the bot should respond to this message.

        Args:
            text: Message text
            user_id: Sender's user ID
            is_group: Whether this is a group chat
            is_reply_to_bot: Whether this is a reply to a bot message
            session: Optional session for reply chain checking
            reply_to_message_id: ID of message being replied to

        Returns:
            True if bot should respond
        """
        # DMs always get responses
        if not is_group:
            return True

        # Mode: always
        if self.mode == "always":
            return True

        # Mode: admin_only
        if self.mode == "admin_only":
            return user_id in self.admin_users

        # Mode: mention (default)
        # Check for @mention
        if f"@{self.bot_username}" in text.lower():
            return True

        # Check if replying to bot
        if is_reply_to_bot:
            return True

        # Check if in existing reply chain
        if session and reply_to_message_id:
            if session.is_in_reply_chain(reply_to_message_id):
                return True

        return False

    def strip_mention(self, text: str) -> str:
        """Remove bot mention from text."""
        import re

        pattern = rf"@{re.escape(self.bot_username)}\s*"
        return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


# =============================================================================
# GLOBAL INSTANCES
# =============================================================================

_session_manager: SessionManager | None = None
_mention_gate: MentionGate | None = None


def get_session_manager() -> SessionManager:
    """Get global session manager."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


def get_mention_gate(bot_username: str = "navig_bot") -> MentionGate:
    """Get global mention gate."""
    global _mention_gate
    if _mention_gate is None:
        _mention_gate = MentionGate(bot_username)
    return _mention_gate
