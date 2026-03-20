"""NAVIG Vault Session — in-memory master key with TTL eviction.

The vault operates in two modes:
  - Machine-fingerprint mode (always available, no unlock needed)
  - Passphrase mode (explicit unlock required; session stored here)

Session is process-memory-only.  Daemon restart requires re-unlock.
Thread-safe via a module-level lock.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

__all__ = ["VaultSession", "SessionStore"]

_DEFAULT_TTL = 1800  # 30 minutes


@dataclass(slots=True)
class VaultSession:
    """Active vault session.  Created by ``navig vault unlock``."""

    master_key: bytes
    unlocked_at: datetime
    ttl_seconds: int = _DEFAULT_TTL
    last_used: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_expired(self) -> bool:
        """True if the session has been idle longer than its TTL."""
        idle = (datetime.now(timezone.utc) - self.last_used).total_seconds()
        return idle > self.ttl_seconds

    def touch(self) -> None:
        """Reset idle timer on use."""
        self.last_used = datetime.now(timezone.utc)

    def remaining_seconds(self) -> int:
        """Seconds until this session expires (0 if already expired)."""
        idle = (datetime.now(timezone.utc) - self.last_used).total_seconds()
        remaining = self.ttl_seconds - idle
        return max(0, int(remaining))

    def ttl_display(self) -> str:
        """Human-readable remaining TTL."""
        rem = self.remaining_seconds()
        if rem == 0:
            return "expired"
        m, s = divmod(rem, 60)
        return f"{m}m {s:02d}s" if m else f"{s}s"


class SessionStore:
    """Thread-safe singleton holding the active :class:`VaultSession`.

    Only one session is active at a time.  Expired sessions are cleared
    automatically on ``get()``.

    Usage
    -----
    session = SessionStore.get()        # None if locked / expired
    SessionStore.set(session)           # call after unlock
    SessionStore.clear()                # explicit lock
    SessionStore.is_unlocked() → bool
    """

    _lock: threading.Lock = threading.Lock()
    _session: Optional[VaultSession] = None

    @classmethod
    def set(cls, session: VaultSession) -> None:
        """Activate a new session."""
        with cls._lock:
            cls._session = session

    @classmethod
    def get(cls) -> Optional[VaultSession]:
        """Return the active session, or ``None`` if locked or expired."""
        with cls._lock:
            if cls._session is None:
                return None
            if cls._session.is_expired():
                cls._session = None
                return None
            cls._session.touch()
            return cls._session

    @classmethod
    def clear(cls) -> None:
        """Explicitly lock the vault (discard session key from memory)."""
        with cls._lock:
            cls._session = None

    @classmethod
    def is_unlocked(cls) -> bool:
        """True if a valid non-expired session exists."""
        return cls.get() is not None

    @classmethod
    def status(cls) -> dict:
        """Return a status dict for ``navig vault doctor`` / ``navig vault unlock``."""
        with cls._lock:
            if cls._session is None:
                return {"locked": True, "ttl": None, "unlocked_at": None}
            if cls._session.is_expired():
                cls._session = None
                return {"locked": True, "ttl": None, "unlocked_at": None}
            return {
                "locked": False,
                "ttl": cls._session.ttl_display(),
                "remaining_seconds": cls._session.remaining_seconds(),
                "unlocked_at": cls._session.unlocked_at.isoformat(),
            }
