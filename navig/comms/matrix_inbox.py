"""
Matrix → NAVIG Inbox Bridge

Listens for incoming Matrix messages and persists them as markdown files
in ``.navig/plans/inbox/`` where the InboxRouterAgent can classify and
route them automatically.

Also surfaces unread Matrix messages via ``navig inbox matrix`` commands.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Maximum message body length to persist (prevent abuse)
MAX_BODY_LENGTH = 8_000

# The subdirectory under .navig/plans/inbox/ for Matrix messages
MATRIX_INBOX_DIR = "matrix"


def _sanitize_filename(text: str, max_len: int = 40) -> str:
    """Turn arbitrary text into a safe filename slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:max_len] or "matrix-message"


def _format_inbox_md(
    *,
    sender: str,
    room_id: str,
    room_name: str,
    body: str,
    timestamp: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Format a Matrix message as a NAVIG inbox markdown file."""
    ts = timestamp or datetime.now(tz=timezone.utc)
    ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    meta = metadata or {}
    extra_yaml = ""
    if meta:
        for k, v in meta.items():
            extra_yaml += f"\n{k}: {v}"

    return f"""---
type: matrix_message
source: matrix
sender: "{sender}"
room_id: "{room_id}"
room_name: "{room_name}"
created: {ts_str}
status: unread{extra_yaml}
---

# Matrix Message from {sender}

**Room**: {room_name} ({room_id})
**Time**: {ts_str}

---

{body[:MAX_BODY_LENGTH]}
"""


class MatrixInboxBridge:
    """
    Bridges Matrix messages into the NAVIG inbox pipeline.

    Usage::

        bridge = MatrixInboxBridge(project_root=Path("/my/project"))
        bot.on_message(bridge.on_matrix_message)

    Each message is persisted as a ``.md`` file under
    ``<project_root>/.navig/plans/inbox/matrix/``.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        auto_process: bool = False,
        filter_rooms: Optional[List[str]] = None,
        filter_senders: Optional[List[str]] = None,
        ignore_notices: bool = True,
    ):
        self.project_root = Path(project_root)
        self.auto_process = auto_process
        self.filter_rooms = set(filter_rooms) if filter_rooms else None
        self.filter_senders = set(filter_senders) if filter_senders else None
        self.ignore_notices = ignore_notices

        self._inbox_dir = self.project_root / ".navig" / "plans" / "inbox" / MATRIX_INBOX_DIR
        self._inbox_dir.mkdir(parents=True, exist_ok=True)

        self._message_count = 0
        self._on_persist_callbacks: List[Callable] = []

    @property
    def inbox_dir(self) -> Path:
        return self._inbox_dir

    def on_persist(self, callback: Callable) -> None:
        """Register a callback called after each message is persisted:
        ``fn(file_path: Path, sender: str, room_id: str)``."""
        self._on_persist_callbacks.append(callback)

    async def on_matrix_message(
        self,
        room_id: str,
        sender: str,
        body: str,
        *,
        room_name: str = "",
        msg_type: str = "m.text",
        event_id: str = "",
    ) -> Optional[Path]:
        """
        Callback compatible with ``NavigMatrixBot.on_message()``.

        Persists the message as a markdown file and optionally triggers
        the InboxRouterAgent for auto-classification.

        Returns the path of the created file, or None if filtered.
        """
        # Filter by room
        if self.filter_rooms and room_id not in self.filter_rooms:
            return None

        # Filter by sender
        if self.filter_senders and sender not in self.filter_senders:
            return None

        # Ignore notices (bot messages)
        if self.ignore_notices and msg_type == "m.notice":
            return None

        # Skip very short messages
        if not body or len(body.strip()) < 2:
            return None

        # Build markdown content
        ts = datetime.now(tz=timezone.utc)
        md_content = _format_inbox_md(
            sender=sender,
            room_id=room_id,
            room_name=room_name or room_id,
            body=body,
            timestamp=ts,
            metadata={"event_id": event_id} if event_id else None,
        )

        # Write file
        self._message_count += 1
        ts_slug = ts.strftime("%Y%m%d-%H%M%S")
        slug = _sanitize_filename(body[:60])
        filename = f"{ts_slug}-{slug}.md"
        file_path = self._inbox_dir / filename

        try:
            file_path.write_text(md_content, encoding="utf-8")
            logger.info("Matrix inbox: persisted %s", file_path.name)
        except Exception:
            logger.exception("Matrix inbox: failed to write %s", filename)
            return None

        # Notify callbacks
        for cb in self._on_persist_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(file_path, sender, room_id)
                else:
                    cb(file_path, sender, room_id)
            except Exception:
                logger.exception("Matrix inbox: persist callback error")

        # Auto-process via InboxRouterAgent if enabled
        if self.auto_process:
            await self._auto_process(file_path)

        return file_path

    async def _auto_process(self, file_path: Path) -> None:
        """Run InboxRouterAgent on the persisted file."""
        try:
            from navig.agents.inbox_router import InboxRouterAgent, execute_plan

            agent = InboxRouterAgent(self.project_root, use_llm=False)
            plan = agent.process_single(file_path, dry_run=False)
            if not plan.get("error"):
                execute_plan(self.project_root, plan, dry_run=False, move_source=True)
                logger.info("Matrix inbox: auto-routed %s -> %s", file_path.name, plan.get("target_path"))
        except Exception:
            logger.exception("Matrix inbox: auto-process failed for %s", file_path.name)

    # ── Query methods for CLI ──

    def list_messages(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List persisted Matrix inbox messages."""
        messages = []
        if not self._inbox_dir.exists():
            return messages

        files = sorted(self._inbox_dir.glob("*.md"), reverse=True)
        for f in files[:limit]:
            try:
                content = f.read_text(encoding="utf-8")
                meta = self._parse_frontmatter(content)

                if status and meta.get("status") != status:
                    continue

                messages.append({
                    "file": f.name,
                    "path": str(f),
                    "sender": meta.get("sender", "?"),
                    "room_id": meta.get("room_id", "?"),
                    "room_name": meta.get("room_name", "?"),
                    "created": meta.get("created", "?"),
                    "status": meta.get("status", "unread"),
                    "preview": self._extract_body_preview(content),
                })
            except Exception:
                logger.warning("Matrix inbox: couldn't parse %s", f.name)

        return messages

    def mark_read(self, filename: str) -> bool:
        """Mark a message as read by updating its frontmatter."""
        fp = self._inbox_dir / filename
        if not fp.exists():
            return False
        try:
            content = fp.read_text(encoding="utf-8")
            content = content.replace("status: unread", "status: read", 1)
            fp.write_text(content, encoding="utf-8")
            return True
        except Exception:
            return False

    def mark_all_read(self) -> int:
        """Mark all messages as read. Returns count."""
        count = 0
        for f in self._inbox_dir.glob("*.md"):
            if self.mark_read(f.name):
                count += 1
        return count

    def get_unread_count(self) -> int:
        """Count unread messages."""
        return len(self.list_messages(status="unread"))

    def delete_message(self, filename: str) -> bool:
        """Delete a persisted message."""
        fp = self._inbox_dir / filename
        if fp.exists():
            fp.unlink()
            return True
        return False

    def purge_read(self) -> int:
        """Delete all read messages. Returns count."""
        count = 0
        for f in self._inbox_dir.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                meta = self._parse_frontmatter(content)
                if meta.get("status") == "read":
                    f.unlink()
                    count += 1
            except Exception:
                pass
        return count

    # ── Internal ──

    @staticmethod
    def _parse_frontmatter(content: str) -> Dict[str, str]:
        """Extract YAML frontmatter as a flat dict."""
        result: Dict[str, str] = {}
        if not content.startswith("---"):
            return result
        end = content.find("---", 3)
        if end == -1:
            return result
        fm = content[3:end].strip()
        for line in fm.split("\n"):
            if ":" in line:
                key, _, val = line.partition(":")
                result[key.strip()] = val.strip().strip('"')
        return result

    @staticmethod
    def _extract_body_preview(content: str, max_len: int = 80) -> str:
        """Extract a short preview from the body text (after frontmatter)."""
        # Skip frontmatter
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                content = content[end + 3:]
        # Skip headers
        lines = [
            ln.strip()
            for ln in content.strip().split("\n")
            if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("**")
            and ln.strip() != "---"
        ]
        text = " ".join(lines)
        return text[:max_len] + ("..." if len(text) > max_len else "")


def get_inbox_bridge(project_root: Optional[Path] = None) -> MatrixInboxBridge:
    """Factory for MatrixInboxBridge with sensible defaults."""
    if project_root is None:
        # Walk up to find .navig/
        cwd = Path.cwd()
        for p in [cwd, *cwd.parents]:
            if (p / ".navig").is_dir():
                project_root = p
                break
        else:
            project_root = cwd

    return MatrixInboxBridge(project_root)
