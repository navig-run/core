"""
navig.agent.session_store — Persistent session storage with resume capability.

Stores conversation sessions as NDJSON (newline-delimited JSON) files in
``~/.navig/sessions/``.  Each line is a serialised :class:`SessionEntry`.
A companion ``.meta.json`` sidecar holds :class:`SessionMetadata`.

Key features:

* **Append-only storage**: Entries are appended atomically (one ``write`` per
  turn), so a crash never corrupts earlier turns.
* **Compact boundary markers**: When :class:`~navig.agent.context_compressor.ReactiveCompactor`
  fires, the session records a boundary so that :meth:`SessionStore.resume`
  can start from the most recent snapshot rather than replaying every turn.
* **Session listing**: :meth:`SessionStore.list_sessions` returns most-recent
  sessions across all workspaces; :meth:`SessionStore.find_by_workspace`
  narrows to a specific project.
* **Auto-cleanup**: :func:`cleanup_old_sessions` removes sessions older than
  a configurable TTL (default 90 days).

Usage::

    from navig.agent.session_store import SessionStore, SessionEntry

    store = SessionStore()                        # auto-generated ID
    store.append(SessionEntry(role="user", content="Hello"))
    store.append(SessionEntry(role="assistant", content="Hi there"))
    store.finalize(summary="Greeting exchange")

    # Later — resume
    store2 = SessionStore(session_id=store.session_id)
    messages = store2.resume()
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Default base directory
# ─────────────────────────────────────────────────────────────

_DEFAULT_BASE_DIR = Path.home() / ".navig" / "sessions"

# ─────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────


@dataclass
class SessionEntry:
    """A single turn in a session transcript.

    Args:
        role:               Message role (``user``, ``assistant``, ``tool``, ``system``).
        content:            Message text content.
        timestamp:          Unix epoch seconds (auto-set to ``time.time()`` when omitted).
        tool_calls:         Tool-call dicts attached to assistant messages.
        tool_results:       Tool-result dicts (for ``tool`` role messages).
        is_compact_boundary: ``True`` when a reactive compaction happened here.
        tokens_used:        Estimated token count for this turn.
        model:              Model identifier used for this turn.
        cost:               Estimated cost in USD.
    """

    role: str
    content: str = ""
    timestamp: float = 0.0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    is_compact_boundary: bool = False
    tokens_used: int = 0
    model: str = ""
    cost: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    # ── Serialisation ──

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (JSON-safe)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionEntry:
        """Deserialise from a dict."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    # ── Conversion ──

    def to_message(self) -> dict[str, Any]:
        """Convert to an LLM message dict suitable for injection into a conversation."""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.role == "tool" and self.tool_results:
            # Flatten first tool_result's tool_call_id if present
            first = self.tool_results[0] if self.tool_results else {}
            if "tool_call_id" in first:
                msg["tool_call_id"] = first["tool_call_id"]
        return msg


@dataclass
class SessionMetadata:
    """Session-level metadata persisted in a sidecar ``.meta.json`` file."""

    session_id: str
    created_at: float = 0.0
    last_active: float = 0.0
    turn_count: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    summary: str = ""
    workspace: str = ""
    tags: list[str] = field(default_factory=list)
    finalized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMetadata:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ─────────────────────────────────────────────────────────────
# Session ID generation
# ─────────────────────────────────────────────────────────────


def _generate_session_id() -> str:
    """Generate a human-readable, sortable, unique session ID.

    Format: ``YYYYMMDD_HHMMSS_<4-hex>``
    Example: ``20250124_143022_a7f3``
    """
    import secrets
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    suffix = secrets.token_hex(2)  # 4 hex chars
    return f"{ts}_{suffix}"


# ─────────────────────────────────────────────────────────────
# SessionStore
# ─────────────────────────────────────────────────────────────


class SessionStore:
    """Append-only NDJSON session storage with resume support.

    Args:
        session_id: Explicit session ID.  If *None*, a new one is generated.
        base_dir:   Override the default ``~/.navig/sessions/`` directory.
                    Useful for testing.
        workspace:  Working directory associated with this session.
    """

    def __init__(
        self,
        session_id: str | None = None,
        base_dir: Path | None = None,
        workspace: str = "",
    ) -> None:
        self._base_dir = base_dir or _DEFAULT_BASE_DIR
        self.session_id = session_id or _generate_session_id()
        self.file = self._base_dir / f"{self.session_id}.jsonl"
        self.meta_file = self._base_dir / f"{self.session_id}.meta.json"
        self._workspace = workspace
        self._meta: SessionMetadata | None = None

    # ── Write operations ────────────────────────────────────

    def append(self, entry: SessionEntry) -> None:
        """Append a single entry to the session NDJSON file.

        Creates the directory and metadata file on the first append.
        """
        self.file.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(entry.to_dict(), ensure_ascii=False)
        with open(self.file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        # Update metadata
        meta = self._load_or_create_meta()
        meta.turn_count += 1
        meta.last_active = entry.timestamp or time.time()
        meta.total_tokens += entry.tokens_used
        meta.total_cost += entry.cost
        self._save_meta(meta)

    def mark_compact_boundary(self) -> None:
        """Record a compact boundary marker.

        Inserts a zero-content system entry flagged with
        ``is_compact_boundary = True``.
        """
        self.append(SessionEntry(
            role="system",
            content="[compact boundary]",
            is_compact_boundary=True,
        ))

    def finalize(self, summary: str = "") -> None:
        """Mark the session as finalized with an optional summary."""
        meta = self._load_or_create_meta()
        meta.finalized = True
        if summary:
            meta.summary = summary
        meta.last_active = time.time()
        self._save_meta(meta)

    # ── Read operations ─────────────────────────────────────

    def load(self) -> list[SessionEntry]:
        """Load all entries from the session NDJSON file.

        Returns an empty list if the file does not exist.
        Invalid JSON lines are skipped with a debug log.
        """
        if not self.file.exists():
            return []
        entries: list[SessionEntry] = []
        for lineno, line in enumerate(
            self.file.read_text(encoding="utf-8").splitlines(), 1
        ):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(SessionEntry.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError) as exc:
                logger.debug("Skipping invalid line %d in %s: %s", lineno, self.file.name, exc)
        return entries

    def resume(self, max_entries: int = 50) -> list[dict[str, Any]]:
        """Load the session for resume — returns LLM message-format dicts.

        Resumes from the **last compact boundary** so that only the
        most recent window of context is loaded.  Falls back to the
        last *max_entries* turns if no boundary exists.

        Args:
            max_entries: Maximum number of entries to return.

        Returns:
            A list of message dicts (``role``/``content``/``tool_calls``).
        """
        entries = self.load()
        if not entries:
            return []

        # Find last compact boundary
        last_boundary = 0
        for i, e in enumerate(entries):
            if e.is_compact_boundary:
                last_boundary = i + 1  # start after the boundary marker

        # Slice from boundary, limit to max_entries
        recent = entries[last_boundary:]
        if len(recent) > max_entries:
            recent = recent[-max_entries:]

        # Filter out boundary markers themselves from output
        return [
            e.to_message()
            for e in recent
            if not e.is_compact_boundary
        ]

    def get_metadata(self) -> SessionMetadata:
        """Return the session metadata (loads from disk if needed)."""
        return self._load_or_create_meta()

    # ── Class-level queries ─────────────────────────────────

    @classmethod
    def list_sessions(
        cls,
        limit: int = 20,
        base_dir: Path | None = None,
    ) -> list[SessionMetadata]:
        """List recent sessions ordered by last activity (newest first).

        Args:
            limit:    Maximum number of sessions to return.
            base_dir: Override the default sessions directory.

        Returns:
            A list of :class:`SessionMetadata` sorted newest-first.
        """
        directory = base_dir or _DEFAULT_BASE_DIR
        if not directory.exists():
            return []

        metas: list[SessionMetadata] = []
        for path in directory.glob("*.meta.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                metas.append(SessionMetadata.from_dict(data))
            except (json.JSONDecodeError, TypeError, OSError) as exc:
                logger.debug("Skipping invalid meta %s: %s", path.name, exc)

        # Sort by last_active descending
        metas.sort(key=lambda m: m.last_active, reverse=True)
        return metas[:limit]

    @classmethod
    def find_by_workspace(
        cls,
        workspace: str,
        base_dir: Path | None = None,
    ) -> list[SessionMetadata]:
        """Find sessions associated with a workspace path.

        Args:
            workspace: Workspace directory path (substring match).
            base_dir:  Override the default sessions directory.

        Returns:
            Matching sessions sorted newest-first.
        """
        all_sessions = cls.list_sessions(limit=1000, base_dir=base_dir)
        workspace_norm = os.path.normpath(workspace).lower()
        return [
            m for m in all_sessions
            if workspace_norm in os.path.normpath(m.workspace).lower()
        ]

    @classmethod
    def get_latest(
        cls,
        base_dir: Path | None = None,
        workspace: str | None = None,
    ) -> SessionStore | None:
        """Return a :class:`SessionStore` for the most recent session.

        If *workspace* is given, scopes to that workspace.

        Returns:
            A ``SessionStore`` instance, or ``None`` if no sessions exist.
        """
        if workspace:
            metas = cls.find_by_workspace(workspace, base_dir=base_dir)
        else:
            metas = cls.list_sessions(limit=1, base_dir=base_dir)
        if not metas:
            return None
        return cls(session_id=metas[0].session_id, base_dir=base_dir)

    # ── Internals ───────────────────────────────────────────

    def _load_or_create_meta(self) -> SessionMetadata:
        """Load existing metadata or create a fresh instance."""
        if self._meta is not None:
            return self._meta
        if self.meta_file.exists():
            try:
                data = json.loads(self.meta_file.read_text(encoding="utf-8"))
                self._meta = SessionMetadata.from_dict(data)
                return self._meta
            except (json.JSONDecodeError, TypeError, OSError) as exc:
                logger.debug("Failed to load meta %s: %s", self.meta_file.name, exc)

        self._meta = SessionMetadata(
            session_id=self.session_id,
            created_at=time.time(),
            last_active=time.time(),
            workspace=self._workspace,
        )
        self._save_meta(self._meta)
        return self._meta

    def _save_meta(self, meta: SessionMetadata) -> None:
        """Persist metadata to the sidecar file."""
        self._meta = meta
        self.meta_file.parent.mkdir(parents=True, exist_ok=True)
        self.meta_file.write_text(
            json.dumps(meta.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


# ─────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────


def cleanup_old_sessions(
    max_age_days: int = 90,
    base_dir: Path | None = None,
) -> int:
    """Remove sessions older than *max_age_days*.

    Returns the number of sessions removed.
    """
    directory = base_dir or _DEFAULT_BASE_DIR
    if not directory.exists():
        return 0

    cutoff = time.time() - (max_age_days * 86_400)
    removed = 0

    for meta_path in directory.glob("*.meta.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            last_active = data.get("last_active", 0.0)
        except (json.JSONDecodeError, OSError):
            last_active = 0.0

        if last_active < cutoff:
            sid = meta_path.stem.replace(".meta", "")
            jsonl_path = directory / f"{sid}.jsonl"
            try:
                meta_path.unlink(missing_ok=True)
                jsonl_path.unlink(missing_ok=True)
                removed += 1
                logger.debug("Cleaned up old session %s", sid)
            except OSError as exc:
                logger.debug("Failed to remove session %s: %s", sid, exc)

    return removed
