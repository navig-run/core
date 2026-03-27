"""
NAVIG Storage Engine — Unified SQLite infrastructure.

Provides:
    Engine          Connection factory with PRAGMA profiles and prepared stmt cache
    PragmaProfile   Tiered PRAGMA configurations (FAST / BALANCED / DURABLE)
    WriteBatcher    Time-and-count-triggered batch commit queue
    QueryTimer      p50/p95/p99 latency tracking with slow-query logging
    TxHelper        Context managers for BEGIN IMMEDIATE, SAVEPOINT/RELEASE
    MigrationRunner Forward-only SQL schema migrations with dry-run
"""

from navig.storage.engine import Engine, get_engine
from navig.storage.pragma_profiles import BALANCED, DURABLE, FAST, PragmaProfile
from navig.storage.query_timer import QueryTimer
from navig.storage.tx_helpers import begin_immediate, savepoint
from navig.storage.write_batcher import WriteBatcher

__all__ = [
    "Engine",
    "get_engine",
    "PragmaProfile",
    "FAST",
    "BALANCED",
    "DURABLE",
    "WriteBatcher",
    "QueryTimer",
    "begin_immediate",
    "savepoint",
]
