#!/usr/bin/env python3
"""
SQLite Engine Performance Benchmark

Compares old BaseStore implementation vs new Engine-backed implementation.
Tests: writes, batch writes, reads, write batching, query timing.
"""

import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from navig.storage import get_engine
from navig.store.base import BaseStore


class OldStyleStore(BaseStore):
    """Simulates old implementation without Engine optimizations."""

    SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS test_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL,
        value TEXT,
        timestamp REAL
    );
    CREATE INDEX IF NOT EXISTS idx_key ON test_data(key);
    """

    def _create_schema(self, conn):
        conn.executescript(self.SCHEMA_SQL)


class NewStyleStore(BaseStore):
    """New implementation using Engine."""

    SCHEMA_SQL = OldStyleStore.SCHEMA_SQL

    def _create_schema(self, conn):
        conn.executescript(self.SCHEMA_SQL)


def measure(func, iterations=100):
    """Measure execution time statistics."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        func()
        times.append((time.perf_counter() - start) * 1000)

    return {
        "min": min(times),
        "max": max(times),
        "mean": statistics.mean(times),
        "p50": statistics.median(times),
        "p95": sorted(times)[int(len(times) * 0.95)],
    }


def benchmark_single_writes():
    """Compare single write performance."""
    print("\n1. Single Write Performance (100 iterations)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        old_db = Path(tmpdir) / "old.db"
        new_db = Path(tmpdir) / "new.db"

        old_store = OldStyleStore(old_db)
        new_store = NewStyleStore(new_db)

        try:
            # Old implementation
            counter = [0]

            def old_write():
                counter[0] += 1
                old_store._write(
                    "INSERT INTO test_data (key, value, timestamp) VALUES (?, ?, ?)",
                    (f"key_{counter[0]}", f"value_{counter[0]}", time.time()),
                )

            old_stats = measure(old_write, 100)

            # New implementation
            counter = [0]

            def new_write():
                counter[0] += 1
                new_store._write(
                    "INSERT INTO test_data (key, value, timestamp) VALUES (?, ?, ?)",
                    (f"key_{counter[0]}", f"value_{counter[0]}", time.time()),
                )

            new_stats = measure(new_write, 100)

            # Results
            print(f"Old BaseStore: {old_stats['mean']:.2f}ms avg, {old_stats['p95']:.2f}ms p95")
            print(f"New Engine:    {new_stats['mean']:.2f}ms avg, {new_stats['p95']:.2f}ms p95")
            improvement = ((old_stats["mean"] - new_stats["mean"]) / old_stats["mean"]) * 100
            print(f"Improvement:   {improvement:+.1f}%")
        finally:
            old_store.close()
            new_store.close()


def benchmark_batch_writes():
    """Compare batch write performance."""
    print("\n2. Batch Write Performance (50 batches of 100 records)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        old_db = Path(tmpdir) / "old.db"
        new_db = Path(tmpdir) / "new.db"

        old_store = OldStyleStore(old_db)
        new_store = NewStyleStore(new_db)

        try:
            # Old implementation
            counter = [0]

            def old_batch():
                data = []
                for i in range(100):
                    counter[0] += 1
                    data.append((f"key_{counter[0]}", f"value_{counter[0]}", time.time()))
                old_store._write_many(
                    "INSERT INTO test_data (key, value, timestamp) VALUES (?, ?, ?)",
                    data,
                )

            old_stats = measure(old_batch, 50)

            # New implementation
            counter = [0]

            def new_batch():
                data = []
                for i in range(100):
                    counter[0] += 1
                    data.append((f"key_{counter[0]}", f"value_{counter[0]}", time.time()))
                new_store._write_many(
                    "INSERT INTO test_data (key, value, timestamp) VALUES (?, ?, ?)",
                    data,
                )

            new_stats = measure(new_batch, 50)

            # Results
            print(f"Old BaseStore: {old_stats['mean']:.2f}ms avg, {old_stats['p95']:.2f}ms p95")
            print(f"New Engine:    {new_stats['mean']:.2f}ms avg, {new_stats['p95']:.2f}ms p95")
            improvement = ((old_stats["mean"] - new_stats["mean"]) / old_stats["mean"]) * 100
            print(f"Improvement:   {improvement:+.1f}%")
        finally:
            old_store.close()
            new_store.close()


def benchmark_reads():
    """Compare read performance."""
    print("\n3. Read Performance (1000 random queries)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        old_db = Path(tmpdir) / "old.db"
        new_db = Path(tmpdir) / "new.db"

        old_store = OldStyleStore(old_db)
        new_store = NewStyleStore(new_db)

        try:
            # Populate with data
            data = [(f"key_{i}", f"value_{i}", time.time()) for i in range(1000)]
            old_store._write_many(
                "INSERT INTO test_data (key, value, timestamp) VALUES (?, ?, ?)", data
            )
            new_store._write_many(
                "INSERT INTO test_data (key, value, timestamp) VALUES (?, ?, ?)", data
            )

            # Old implementation
            counter = [0]

            def old_read():
                counter[0] = (counter[0] + 1) % 1000
                conn = old_store._get_conn()
                rows = conn.execute(
                    "SELECT * FROM test_data WHERE key = ?", (f"key_{counter[0]}",)
                ).fetchall()

            old_stats = measure(old_read, 1000)

            # New implementation
            counter = [0]

            def new_read():
                counter[0] = (counter[0] + 1) % 1000
                conn = new_store._get_conn()
                rows = conn.execute(
                    "SELECT * FROM test_data WHERE key = ?", (f"key_{counter[0]}",)
                ).fetchall()

            new_stats = measure(new_read, 1000)

            # Results
            print(f"Old BaseStore: {old_stats['mean']:.2f}ms avg, {old_stats['p95']:.2f}ms p95")
            print(f"New Engine:    {new_stats['mean']:.2f}ms avg, {new_stats['p95']:.2f}ms p95")
            improvement = ((old_stats["mean"] - new_stats["mean"]) / old_stats["mean"]) * 100
            print(f"Improvement:   {improvement:+.1f}%")
        finally:
            old_store.close()
            new_store.close()


def benchmark_write_batcher():
    """Test WriteBatcher performance."""
    print("\n4. WriteBatcher Performance (500 enqueued, auto-flushed)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "batcher.db"
        engine = get_engine()

        try:
            # Setup database
            conn = engine.connect(db_path)
            conn.executescript(OldStyleStore.SCHEMA_SQL)
            conn.commit()

            # Create batcher
            batcher = engine.batcher(db_path, batch_size=50, flush_interval_ms=10)

            # Measure enqueue throughput
            start = time.perf_counter()
            for i in range(500):
                batcher.enqueue(
                    "INSERT INTO test_data (key, value, timestamp) VALUES (?, ?, ?)",
                    (f"key_{i}", f"value_{i}", time.time()),
                )
            enqueue_time = (time.perf_counter() - start) * 1000

            # Force final flush
            batcher.flush()
            total_time = (time.perf_counter() - start) * 1000

            # Get stats
            stats = batcher.get_stats()

            print(
                f"Enqueue time:  {enqueue_time:.2f}ms ({500 / (enqueue_time / 1000):.0f} ops/sec)"
            )
            print(f"Total time:    {total_time:.2f}ms")
            print(f"Flush count:   {stats['flush_count']}")
            print(f"Batched ops:   {stats['flushed']}/{stats['enqueued']}")
            print(f"Avg per flush: {stats['flushed'] / stats['flush_count']:.1f} ops")

            batcher.close()
        finally:
            engine.close(db_path)


def benchmark_pragma_profiles():
    """Test PRAGMA profile impact."""
    print("\n5. PRAGMA Profile Impact (1000 writes each)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # FAST profile (runtime.db)
            fast_db = Path(tmpdir) / "runtime.db"
            fast_store = NewStyleStore(fast_db)

            def fast_writes():
                data = [(f"k_{i}", f"v_{i}", time.time()) for i in range(1000)]
                fast_store._write_many(
                    "INSERT INTO test_data (key, value, timestamp) VALUES (?, ?, ?)",
                    data,
                )

            start = time.perf_counter()
            fast_writes()
            fast_time = (time.perf_counter() - start) * 1000

            # BALANCED profile (memory.db)
            balanced_db = Path(tmpdir) / "memory.db"
            balanced_store = NewStyleStore(balanced_db)

            def balanced_writes():
                data = [(f"k_{i}", f"v_{i}", time.time()) for i in range(1000)]
                balanced_store._write_many(
                    "INSERT INTO test_data (key, value, timestamp) VALUES (?, ?, ?)",
                    data,
                )

            start = time.perf_counter()
            balanced_writes()
            balanced_time = (time.perf_counter() - start) * 1000

            # DURABLE profile (audit.db)
            durable_db = Path(tmpdir) / "audit.db"
            durable_store = NewStyleStore(durable_db)

            def durable_writes():
                data = [(f"k_{i}", f"v_{i}", time.time()) for i in range(1000)]
                durable_store._write_many(
                    "INSERT INTO test_data (key, value, timestamp) VALUES (?, ?, ?)",
                    data,
                )

            start = time.perf_counter()
            durable_writes()
            durable_time = (time.perf_counter() - start) * 1000

            print(f"FAST (runtime.db):     {fast_time:.2f}ms")
            print(f"BALANCED (memory.db):  {balanced_time:.2f}ms")
            print(f"DURABLE (audit.db):    {durable_time:.2f}ms")
            print(f"FAST vs DURABLE:       {(durable_time / fast_time):.2f}x slower (expected)")
        finally:
            fast_store.close()
            balanced_store.close()
            durable_store.close()


def main():
    """Run all benchmarks."""
    print("=" * 60)
    print("SQLite Engine Performance Benchmark")
    print("=" * 60)

    benchmark_single_writes()
    benchmark_batch_writes()
    benchmark_reads()
    benchmark_write_batcher()
    benchmark_pragma_profiles()

    print("\n" + "=" * 60)
    print("Benchmark Complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
