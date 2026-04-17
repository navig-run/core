"""
Session Manager - Persistent Conversation Context

Manages agent sessions with:
- Disk persistence (survives restarts)
- Automatic compaction (summarizes old messages)
- Multi-channel session keys
- Host-scoped context for NAVIG
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from navig.core.yaml_io import atomic_write_text
from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


@dataclass
class Session:
    """
    Agent session with conversation history.

    Attributes:
        key: Unique session identifier
        messages: Conversation history
        metadata: Additional session data
        created_at: When session was created
        updated_at: Last activity timestamp
    """

    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """Serialize session to dictionary."""
        return {
            "key": self.key,
            "messages": self.messages,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        """Deserialize session from dictionary."""
        created_at = None
        updated_at = None

        if data.get("created_at"):
            try:
                created_at = datetime.fromisoformat(data["created_at"])
            except (ValueError, TypeError):
                pass  # malformed or missing value; skip

        if data.get("updated_at"):
            try:
                updated_at = datetime.fromisoformat(data["updated_at"])
            except (ValueError, TypeError):
                pass  # malformed or missing value; skip

        return cls(
            key=data.get("key", ""),
            messages=data.get("messages", []),
            metadata=data.get("metadata", {}),
            created_at=created_at,
            updated_at=updated_at,
        )


class NavigSessionKey:
    """
    Build session keys for NAVIG context.

    Format: agent:<agentId>:<channel>:<accountId>:<peerKind>:<peerId>[:host:<hostName>]

    Examples:
        - agent:default:telegram:default:dm:123456789
        - agent:default:discord:default:group:987654321
        - agent:navig:telegram:default:dm:123:host:myserver
    """

    @staticmethod
    def for_dm(agent_id: str, channel: str, user_id: str) -> str:
        """Create DM session key."""
        return f"agent:{agent_id}:{channel}:default:dm:{user_id}"

    @staticmethod
    def for_group(agent_id: str, channel: str, group_id: str) -> str:
        """Create group chat session key."""
        return f"agent:{agent_id}:{channel}:default:group:{group_id}"

    @staticmethod
    def for_host_context(base_key: str, host_name: str) -> str:
        """Add host context to session key."""
        return f"{base_key}:host:{host_name}"

    @staticmethod
    def main_session(agent_id: str = "default") -> str:
        """Main agent session (used by heartbeat)."""
        return f"agent:{agent_id}:main"

    @staticmethod
    def cron_session(job_id: str) -> str:
        """Isolated cron job session."""
        return f"cron:{job_id}"

    @staticmethod
    def parse(session_key: str) -> dict[str, str]:
        """Parse session key into components."""
        parts = session_key.split(":")

        result = {
            "raw": session_key,
            "type": parts[0] if parts else "unknown",
        }

        if result["type"] == "agent" and len(parts) >= 6:
            result.update(
                {
                    "agent_id": parts[1],
                    "channel": parts[2],
                    "account_id": parts[3],
                    "peer_kind": parts[4],
                    "peer_id": parts[5],
                }
            )

            # Check for host suffix
            if len(parts) >= 8 and parts[6] == "host":
                result["host"] = parts[7]

        elif result["type"] == "cron" and len(parts) >= 2:
            result["job_id"] = parts[1]

        return result


class SessionManager:
    """
    Manages persistent agent sessions.

    Features:
    - Disk persistence (JSON files)
    - Automatic compaction when token limit approaches
    - Thread-safe async operations
    - Session expiry for inactive sessions
    """

    def __init__(
        self,
        storage_dir: Path,
        max_message_chars: int = 50000,  # ~12k tokens
        compaction_keep_messages: int = 20,  # Keep last 20 messages
    ):
        """
        Initialize session manager.

        Args:
            storage_dir: Base storage directory
            max_message_chars: Trigger compaction at this char count
            compaction_keep_messages: Messages to keep after compaction
        """
        self.storage_dir = Path(storage_dir) / "sessions"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.max_message_chars = max_message_chars
        self.compaction_keep_messages = compaction_keep_messages

        # In-memory cache
        self.sessions: dict[str, Session] = {}
        self.lock = asyncio.Lock()

        # AI client for summarization (lazy loaded)
        self._ai = None

        logger.info("SessionManager initialized: %s", self.storage_dir)

    def _sanitize_key(self, session_key: str) -> str:
        """Sanitize session key for use as filename."""
        # Replace unsafe characters
        return session_key.replace(":", "_").replace("/", "_").replace("\\", "_")

    def _get_session_file(self, session_key: str) -> Path:
        """Get path to session file."""
        safe_key = self._sanitize_key(session_key)
        return self.storage_dir / f"{safe_key}.json"

    async def get_session(self, session_key: str) -> Session:
        """
        Get or create a session.

        Args:
            session_key: Unique session identifier

        Returns:
            Session object
        """
        async with self.lock:
            # Check cache
            if session_key in self.sessions:
                return self.sessions[session_key]

            # Try to load from disk
            session = await self._load_session(session_key)
            self.sessions[session_key] = session
            return session

    async def _load_session(self, session_key: str) -> Session:
        """Load session from disk or create new."""
        session_file = self._get_session_file(session_key)

        if session_file.exists():
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                logger.debug("Loaded session: %s", session_key)
                return Session.from_dict(data)
            except Exception as e:
                logger.error("Failed to load session %s: %s", session_key, e)

        # Create new session
        logger.debug("Created new session: %s", session_key)
        return Session(key=session_key)

    async def save_session(self, session: Session):
        """Save session to disk (lock covers mutation only; I/O is outside the lock)."""
        # Stamp updated_at and snapshot data under the lock
        async with self.lock:
            session.updated_at = datetime.now()
            data = session.to_dict()  # snapshot while locked

        # Write outside the lock — blocking I/O must not hold the event-loop lock
        session_file = self._get_session_file(session.key)
        try:
            payload = json.dumps(data, indent=2, ensure_ascii=False)
            tmp = session_file.with_suffix(".tmp")
            atomic_write_text(tmp, payload)
            tmp.replace(session_file)  # atomic on POSIX; best-effort on Windows
            logger.debug("Saved session: %s", session.key)
        except Exception as e:
            logger.error("Failed to save session %s: %s", session.key, e)

    async def save_all(self):
        """Save all cached sessions to disk."""
        for session in self.sessions.values():
            await self.save_session(session)
        logger.info("Saved %s sessions", len(self.sessions))

    async def add_message(
        self,
        session_key: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ):
        """
        Add message to session.

        Args:
            session_key: Session identifier
            role: Message role (user, assistant, system)
            content: Message content
            metadata: Optional additional metadata
        """
        session = await self.get_session(session_key)

        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }

        if metadata:
            message["metadata"] = metadata

        session.messages.append(message)

        # Check for compaction
        if self._should_compact(session):
            await self._compact_session(session)

        await self.save_session(session)

    def _should_compact(self, session: Session) -> bool:
        """Check if session needs compaction."""
        total_chars = sum(len(msg.get("content", "")) for msg in session.messages)
        return total_chars > self.max_message_chars

    async def _compact_session(self, session: Session):
        """Summarize old messages to free up context."""
        logger.info("Compacting session: %s", session.key)

        # Keep recent messages
        keep_count = self.compaction_keep_messages
        recent = session.messages[-keep_count:] if len(session.messages) > keep_count else []
        old = (
            session.messages[:-keep_count]
            if len(session.messages) > keep_count
            else session.messages
        )

        if not old:
            return

        # Generate summary
        summary = await self._summarize_messages(old)

        # Replace old messages with summary
        session.messages = [
            {
                "role": "system",
                "content": f"[Previous conversation summary: {summary}]",
                "timestamp": datetime.now().isoformat(),
                "compacted": True,
                "original_count": len(old),
            }
        ] + recent

        logger.info("Compacted %s messages to summary", len(old))

    async def _summarize_messages(self, messages: list[dict]) -> str:
        """Generate AI summary of messages."""
        try:
            from navig.ai import ask_ai

            # Build text to summarize
            text = "\n".join(
                f"{m.get('role', 'unknown')}: {m.get('content', '')[:500]}"
                for m in messages[:50]  # Limit input
            )

            prompt = f"""Summarize this conversation concisely (200 words max).
Focus on key decisions, actions taken, and important context to remember.

Conversation:
{text}

Summary:"""

            summary = await asyncio.get_running_loop().run_in_executor(
                None, lambda: ask_ai(prompt, model="fast")
            )

            return summary.strip()

        except Exception as e:
            logger.error("Summarization failed: %s", e)
            # Fallback: just note what was compacted
            return f"[{len(messages)} earlier messages compacted]"

    async def clear_session(self, session_key: str):
        """Clear all messages from a session."""
        session = await self.get_session(session_key)
        session.messages = []
        session.metadata = {}
        await self.save_session(session)
        logger.info("Cleared session: %s", session_key)

    async def delete_session(self, session_key: str):
        """Delete a session entirely."""
        async with self.lock:
            # Remove from cache
            if session_key in self.sessions:
                del self.sessions[session_key]

            # Remove from disk
            session_file = self._get_session_file(session_key)
            if session_file.exists():
                session_file.unlink()
                logger.info("Deleted session: %s", session_key)

    async def list_sessions(
        self,
        channel: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        List sessions matching criteria.

        Args:
            channel: Filter by channel (telegram, discord, etc.)
            agent_id: Filter by agent ID

        Returns:
            List of session summaries
        """
        sessions = []

        # Scan session files
        for session_file in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                key = data.get("key", "")
                parsed = NavigSessionKey.parse(key)

                # Apply filters
                if channel and parsed.get("channel") != channel:
                    continue
                if agent_id and parsed.get("agent_id") != agent_id:
                    continue

                sessions.append(
                    {
                        "key": key,
                        "parsed": parsed,
                        "message_count": len(data.get("messages", [])),
                        "updated_at": data.get("updated_at"),
                    }
                )

            except Exception as e:
                logger.warning("Failed to read session file %s: %s", session_file, e)

        # Sort by updated_at descending
        sessions.sort(key=lambda s: s.get("updated_at") or "", reverse=True)

        return sessions

    async def get_last_channel(self, agent_id: str = "default") -> str | None:
        """Get the last used channel for an agent."""
        sessions = await self.list_sessions(agent_id=agent_id)

        for session_info in sessions:
            channel = session_info.get("parsed", {}).get("channel")
            if channel and channel not in ("main", "cron"):
                return channel

        return None
