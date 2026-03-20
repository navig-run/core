"""
navig.gateway.session_store — Operator context across channels.

Single-operator design: there is one operator, but they may talk to NAVIG
through several channels simultaneously (Telegram, CLI, VS Code chat …).
The store tracks one active *context window* per channel thread, holding
metadata like the active host, active app, turn counter, and ephemeral KV.

There is **no multi-user isolation** — all contexts belong to the same
operator and may share state when the operator chooses.

Usage
-----
    from navig.gateway.session_store import get_session_store, SessionKey

    store = get_session_store()
    ctx = store.get_or_create(SessionKey("telegram", thread_id="12345"))
    ctx.set("active_host", "production")
    store.touch(ctx.key)

Persistence
-----------
Sessions are in-memory by default.  Pass ``persist_path`` to the store
constructor (or configure ``gateway.session_store_path`` in config) to
enable JSON persistence across daemon restarts.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

__all__ = [
    "SessionKey",
    "OperatorContext",
    "SessionStore",
    "get_session_store",
]

_IDLE_EXPIRY_SECONDS: float = 3600.0  # 1 hour of inactivity


# =============================================================================
# SessionKey — channel-scoped identity
# =============================================================================

@dataclass(frozen=True)
class SessionKey:
    """
    Uniquely identifies a conversation thread.

    For the single-operator model this simply tracks *where* the operator
    is currently talking (e.g. ``("telegram", "chat_id_123")``).
    """
    channel_type: str    # "telegram", "cli", "vscode", "web_ui", …
    thread_id: str = ""  # chat/thread id within the channel; empty = default

    def __str__(self) -> str:
        return f"{self.channel_type}:{self.thread_id}" if self.thread_id else self.channel_type


# =============================================================================
# OperatorContext — per-thread state
# =============================================================================

@dataclass
class OperatorContext:
    """
    Mutable context for one active conversation thread.

    Attributes:
        key:          Channel + thread identity.
        meta:         Arbitrary KV store (active_host, active_app, …).
        created_at:   Unix timestamp when the context was first opened.
        last_active:  Unix timestamp of the last operator interaction.
        turn_count:   Number of completed turns in this context.
    """
    key: SessionKey
    meta: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    turn_count: int = 0

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self.meta.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.meta[key] = value

    def unset(self, key: str) -> None:
        self.meta.pop(key, None)

    def increment_turn(self) -> int:
        self.turn_count += 1
        return self.turn_count

    def is_idle(self, threshold: float = _IDLE_EXPIRY_SECONDS) -> bool:
        return (time.time() - self.last_active) > threshold

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel_type": self.key.channel_type,
            "thread_id": self.key.thread_id,
            "meta": self.meta,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "turn_count": self.turn_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OperatorContext":
        return cls(
            key=SessionKey(
                channel_type=d.get("channel_type", "unknown"),
                thread_id=d.get("thread_id", ""),
            ),
            meta=d.get("meta", {}),
            created_at=d.get("created_at", time.time()),
            last_active=d.get("last_active", time.time()),
            turn_count=d.get("turn_count", 0),
        )


# =============================================================================
# SessionStore
# =============================================================================

class SessionStore:
    """
    Thread-safe in-memory store for OperatorContext objects.

    Optional JSON persistence: pass ``persist_path`` to enable save/load
    across daemon restarts.
    """

    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._contexts: Dict[str, OperatorContext] = {}
        self._persist_path = persist_path
        if persist_path:
            self._load()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def get_or_create(self, key: SessionKey) -> OperatorContext:
        """Return the existing context for *key*, or create a fresh one."""
        k = str(key)
        with self._lock:
            if k not in self._contexts:
                self._contexts[k] = OperatorContext(key=key)
                logger.debug("session_store: created context for %s", k)
            return self._contexts[k]

    def get(self, key: SessionKey) -> Optional[OperatorContext]:
        """Return the context for *key* or ``None`` if it doesn't exist."""
        return self._contexts.get(str(key))

    def touch(self, key: SessionKey) -> None:
        """Update the last_active timestamp for *key* (no-op if not present)."""
        ctx = self._contexts.get(str(key))
        if ctx:
            ctx.last_active = time.time()

    def update(self, key: SessionKey, updates: Dict[str, Any]) -> None:
        """Merge *updates* into the context meta for *key* (creates if absent)."""
        ctx = self.get_or_create(key)
        ctx.meta.update(updates)
        ctx.last_active = time.time()

    def remove(self, key: SessionKey) -> bool:
        """Remove a context. Returns True if it existed."""
        with self._lock:
            removed = self._contexts.pop(str(key), None) is not None
        if removed:
            logger.debug("session_store: removed context for %s", key)
        return removed

    def active_contexts(self) -> List[OperatorContext]:
        """Return all contexts that are not currently idle."""
        with self._lock:
            return [c for c in self._contexts.values() if not c.is_idle()]

    def all_contexts(self) -> List[OperatorContext]:
        with self._lock:
            return list(self._contexts.values())

    def expire_idle(self, threshold: float = _IDLE_EXPIRY_SECONDS) -> int:
        """Remove all idle contexts. Returns the count removed."""
        with self._lock:
            idle_keys = [k for k, c in self._contexts.items() if c.is_idle(threshold)]
            for k in idle_keys:
                del self._contexts[k]
        if idle_keys:
            logger.debug("session_store: expired %d idle context(s)", len(idle_keys))
        return len(idle_keys)

    def save(self) -> None:
        """Persist all contexts to JSON. No-op when no persist_path."""
        if not self._persist_path:
            return
        try:
            data = {k: ctx.to_dict() for k, ctx in self._contexts.items()}
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("session_store: failed to persist: %s", exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for k, d in raw.items():
                ctx = OperatorContext.from_dict(d)
                self._contexts[k] = ctx
            logger.debug("session_store: loaded %d context(s)", len(self._contexts))
        except Exception as exc:
            logger.warning("session_store: failed to load persisted sessions: %s", exc)


# =============================================================================
# Singleton
# =============================================================================

_store_instance: Optional[SessionStore] = None
_store_lock = threading.Lock()


def get_session_store() -> SessionStore:
    """Return the global SessionStore singleton."""
    global _store_instance
    if _store_instance is not None:
        return _store_instance

    with _store_lock:
        if _store_instance is not None:
            return _store_instance

        persist_path: Optional[Path] = None
        try:
            from navig.config import get_config_manager
            raw_path = (
                get_config_manager()
                .global_config
                .get("gateway", {})
                .get("session_store_path", "")
            )
            if raw_path:
                persist_path = Path(raw_path).expanduser()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        _store_instance = SessionStore(persist_path=persist_path)

    return _store_instance


def reset_session_store() -> None:
    """Reset the singleton (used in tests)."""
    global _store_instance
    with _store_lock:
        _store_instance = None
