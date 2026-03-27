"""
PRAGMA Profiles — Three tiered configurations for NAVIG SQLite databases.

Profiles:
    FAST        Ephemeral / cache data. Maximises throughput, accepts data loss.
    BALANCED    Sessions, conversations, matrix state. WAL + NORMAL sync.
    DURABLE     Audit log, vault credentials. Full durability guarantees.

Database → Profile mapping:
    runtime.db          FAST
    memory.db           BALANCED
    matrix.db           BALANCED
    memory/index.db     BALANCED  (FTS + vec, search-critical reads)
    audit.db            DURABLE
    vault.db            DURABLE
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class PragmaProfile:
    """
    Immutable PRAGMA configuration profile.

    Every field maps to a ``PRAGMA <name> = <value>`` statement executed
    on connection open.  ``None`` values are skipped (use SQLite default).
    """

    name: str
    journal_mode: str = "WAL"
    synchronous: str = "NORMAL"
    temp_store: str = "MEMORY"
    cache_size_kb: int = 8192  # negative KiB for SQLite PRAGMA
    mmap_size: int = 0  # bytes; 0 = disabled
    foreign_keys: bool = True
    wal_autocheckpoint: int = 1000  # pages
    busy_timeout: int = 5000  # ms
    page_size: int = 4096  # only effective on new databases
    locking_mode: str = "NORMAL"
    auto_vacuum: str = "NONE"  # NONE | FULL | INCREMENTAL

    def to_pragma_dict(self) -> Dict[str, Any]:
        """Return ``{pragma_name: value}`` ready for ``PRAGMA x = y`` execution."""
        return {
            "journal_mode": self.journal_mode,
            "synchronous": self.synchronous,
            "temp_store": self.temp_store,
            "cache_size": -self.cache_size_kb,  # negative = KiB
            "mmap_size": self.mmap_size,
            "foreign_keys": "ON" if self.foreign_keys else "OFF",
            "wal_autocheckpoint": self.wal_autocheckpoint,
            "busy_timeout": self.busy_timeout,
            "page_size": self.page_size,
            "locking_mode": self.locking_mode,
            "auto_vacuum": self.auto_vacuum,
        }


# ── FAST ──────────────────────────────────────────────────────
# For caches, runtime state, ephemeral data where throughput matters
# more than crash durability.  OFF synchronous = no fsync on commit.

FAST = PragmaProfile(
    name="FAST",
    journal_mode="WAL",
    synchronous="OFF",  # No fsync — OS buffer only
    temp_store="MEMORY",
    cache_size_kb=16384,  # 16 MB — generous for hot cache tables
    mmap_size=67108864,  # 64 MB mmap (Linux/macOS only; auto-disabled on Windows)
    foreign_keys=True,
    wal_autocheckpoint=2000,  # Less frequent checkpointing
    busy_timeout=2000,  # Short timeout — ephemeral data
    page_size=4096,
    locking_mode="NORMAL",
    auto_vacuum="NONE",  # No auto-vacuum overhead
)

# ── BALANCED ──────────────────────────────────────────────────
# For conversation sessions, matrix state, RAG index.  WAL + NORMAL
# synchronous gives crash safety (WAL flushes on checkpoint) with
# good concurrent read throughput.

BALANCED = PragmaProfile(
    name="BALANCED",
    journal_mode="WAL",
    synchronous="NORMAL",  # fsync on WAL checkpoint, not every commit
    temp_store="MEMORY",
    cache_size_kb=32768,  # 32 MB — heavy-read workload
    mmap_size=268435456,  # 256 MB mmap for large index.db
    foreign_keys=True,
    wal_autocheckpoint=1000,  # Standard checkpoint interval
    busy_timeout=5000,  # Moderate patience
    page_size=4096,
    locking_mode="NORMAL",
    auto_vacuum="NONE",
)

# ── DURABLE ───────────────────────────────────────────────────
# For audit log and encrypted vault.  Every commit is fsynced.
# Slower writes but guaranteed crash consistency.

DURABLE = PragmaProfile(
    name="DURABLE",
    journal_mode="WAL",
    synchronous="FULL",  # fsync on EVERY commit
    temp_store="MEMORY",
    cache_size_kb=4096,  # 4 MB — small, rarely read at scale
    mmap_size=0,  # No mmap — vault security, audit safety
    foreign_keys=True,
    wal_autocheckpoint=500,  # Frequent checkpointing — limit WAL size
    busy_timeout=10000,  # Patient — audit writes must not fail
    page_size=4096,
    locking_mode="NORMAL",
    auto_vacuum="INCREMENTAL",  # Reclaim space incrementally (append-heavy)
)

# ── Database → Profile mapping ────────────────────────────────

DB_PROFILES: Dict[str, PragmaProfile] = {
    "runtime.db": FAST,
    "memory.db": BALANCED,
    "matrix.db": BALANCED,
    "index.db": BALANCED,
    "audit.db": DURABLE,
    "vault.db": DURABLE,
}


def profile_for_db(db_filename: str) -> PragmaProfile:
    """
    Return the PRAGMA profile for a database filename.

    Falls back to BALANCED if the database is unknown.
    """
    return DB_PROFILES.get(db_filename, BALANCED)
