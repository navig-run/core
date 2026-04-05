"""
QUANTUM VELOCITY K6 — Auto-Evolutive Performance Profiler (Weaver's Gift)
=========================================================================

The NAVIG daemon profiles itself automatically so it can report its own
bottlenecks and propose future optimizations.

How it works:
    1. Every SAMPLE_EVERY invocations, wrap the CLI call with cProfile.
    2. Extract the top-N hottest functions by cumtime.
    3. Append to ~/.navig/perf/YYYYMMDD.jsonl  (JSON-lines, one entry/run).
    4. `navig evolve status` reads recent entries and detects regressions.
    5. `navig evolve optimize` proposes the next Shadow candidate.

Overhead: < 0.5ms per sampled call (cProfile in deterministic mode is ~2-10%
CPU overhead; we sample 1-in-100 calls, so average overhead is < 0.1ms).

Design contract (Aegis says):
    - Profiling MUST NOT raise or affect the CLI return code.
    - Profile data MUST NOT contain secrets (no args / env vars stored).
    - Profiling MUST be disabled if NAVIG_NO_PROFILE=1 is set.
"""

from __future__ import annotations

import cProfile
import io
import json
import logging
import os
import pstats
import sys
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from navig.platform import paths

logger = logging.getLogger("navig.perf.profiler")

T = TypeVar("T")

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_EVERY: int = 100  # Profile 1 in every N calls
TOP_FUNCTIONS: int = 20  # Number of hot functions to store per sample
PERF_DIR: Path = paths.config_dir() / "perf"
REGRESSION_THRESHOLD_PCT: float = 20.0  # Alert if cumtime grows by > this %

# ─────────────────────────────────────────────────────────────────────────────
# Invocation counter (per-process; reset on daemon restart)
# ─────────────────────────────────────────────────────────────────────────────
_invocation_count: int = 0
_counter_lock = threading.Lock()


def _should_profile() -> bool:
    """Return True for every SAMPLE_EVERY-th call."""
    if os.environ.get("NAVIG_NO_PROFILE") == "1":
        return False
    global _invocation_count
    with _counter_lock:
        _invocation_count += 1
        return (_invocation_count % SAMPLE_EVERY) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Core profiling helpers
# ─────────────────────────────────────────────────────────────────────────────


def profile_call(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Execute *fn* with optional cProfile sampling.

    - On non-sampled calls: zero overhead, returns fn(*args, **kwargs) directly.
    - On sampled calls: wraps with cProfile, extracts stats, writes JSON-lines
      entry to today's perf log.  The result of fn() is always returned.

    Never raises (profiling errors are logged and silently dropped).
    """
    if not _should_profile():
        return fn(*args, **kwargs)

    profiler = cProfile.Profile()
    start = time.perf_counter()
    try:
        result = profiler.runcall(fn, *args, **kwargs)
    except Exception:
        raise
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        # Run stats extraction in background — never block the CLI
        threading.Thread(
            target=_extract_and_store,
            args=(profiler, elapsed_ms),
            daemon=True,
        ).start()
    return result


def _extract_and_store(profiler: cProfile.Profile, elapsed_ms: float) -> None:
    """Extract top functions from *profiler* and append to today's log file."""
    try:
        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats("cumulative")
        stats.print_stats(TOP_FUNCTIONS)

        # Parse the stats into structured dicts
        hot_functions: list[dict[str, Any]] = []
        profiler.create_stats()
        for (filename, lineno, funcname), (_cc, nc, tt, ct, _) in profiler.stats.items():  # type: ignore[union-attr]
            hot_functions.append(
                {
                    "fn": funcname,
                    "file": _strip_prefix(filename),
                    "line": lineno,
                    "calls": nc,
                    "tottime": round(tt * 1000, 3),  # ms
                    "cumtime": round(ct * 1000, 3),  # ms
                }
            )

        # Sort by cumtime descending, keep top N
        hot_functions.sort(key=lambda x: x["cumtime"], reverse=True)
        hot_functions = hot_functions[:TOP_FUNCTIONS]

        argv_safe = _safe_argv()
        entry = {
            "ts": time.time(),
            "cmd": argv_safe,
            "elapsed_ms": round(elapsed_ms, 2),
            "top_fns": hot_functions,
        }

        log_file = PERF_DIR / f"{date.today().isoformat()}.jsonl"
        PERF_DIR.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(entry) + "\n")

    except Exception as exc:
        logger.debug("Profiler extract error: %s", exc)


def _strip_prefix(path: str) -> str:
    """Shorten the file path for display (keep only package-relative part)."""
    for base in ("navig/", "navig\\", "site-packages/"):
        idx = path.rfind(base)
        if idx != -1:
            return path[idx:]
    return os.path.basename(path)


def _safe_argv() -> str:
    """Return the CLI invocation without any potentially sensitive arguments."""
    try:
        args = sys.argv[1:]
        # Keep only the first 2 positional tokens (e.g. "host list")
        return " ".join(args[:2]) if args else "(empty)"
    except Exception:
        return "(unknown)"


# ─────────────────────────────────────────────────────────────────────────────
# Regression detection & reporting (used by `navig evolve status`)
# ─────────────────────────────────────────────────────────────────────────────


def load_recent_samples(days: int = 7) -> list[dict[str, Any]]:
    """Load all profile samples from the last *days* days."""
    samples: list[dict[str, Any]] = []
    today = date.today()
    for i in range(days):
        from datetime import timedelta

        day = today - timedelta(days=i)
        log_file = PERF_DIR / f"{day.isoformat()}.jsonl"
        if not log_file.exists():
            continue
        try:
            with open(log_file, encoding="utf-8") as _f:
                for line in _f:
                    line = line.strip()
                    if line:
                        samples.append(json.loads(line))
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
    return samples


def detect_regressions(samples: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """
    Detect performance regressions in the last 7 days of profile data.

    Returns a list of regression dicts:
        {"cmd": str, "fn": str, "old_ms": float, "new_ms": float, "delta_pct": float}
    """
    if samples is None:
        samples = load_recent_samples()

    if not samples:
        return []

    # Group by command
    by_cmd: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        cmd = s.get("cmd", "")
        by_cmd.setdefault(cmd, []).append(s)

    regressions: list[dict[str, Any]] = []
    for cmd, entries in by_cmd.items():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda x: x["ts"])
        old_entry = entries[0]
        new_entry = entries[-1]

        old_ms = old_entry.get("elapsed_ms", 0)
        new_ms = new_entry.get("elapsed_ms", 0)

        if old_ms > 0 and new_ms > 0:
            delta_pct = ((new_ms - old_ms) / old_ms) * 100
            if delta_pct > REGRESSION_THRESHOLD_PCT:
                # Find the new hottest culprit function
                culprit = ""
                if new_entry.get("top_fns"):
                    culprit = new_entry["top_fns"][0].get("fn", "")
                regressions.append(
                    {
                        "cmd": cmd,
                        "fn": culprit,
                        "old_ms": round(old_ms, 1),
                        "new_ms": round(new_ms, 1),
                        "delta_pct": round(delta_pct, 1),
                    }
                )

    return sorted(regressions, key=lambda r: r["delta_pct"], reverse=True)


def suggest_optimizations(samples: list[dict[str, Any]] | None = None) -> list[str]:
    """
    Return human-readable optimization suggestions based on profile data.
    Used by `navig evolve optimize`.
    """
    if samples is None:
        samples = load_recent_samples()

    if not samples:
        return ["No profile data available yet. Run some NAVIG commands to collect data."]

    # Aggregate cumtime by function across all samples
    fn_cumtime: dict[str, float] = {}
    fn_calls: dict[str, int] = {}
    for sample in samples:
        for fn_info in sample.get("top_fns", []):
            key = f"{fn_info.get('file', '')}:{fn_info.get('fn', '')}"
            fn_cumtime[key] = fn_cumtime.get(key, 0) + fn_info.get("cumtime", 0)
            fn_calls[key] = fn_calls.get(key, 0) + fn_info.get("calls", 0)

    # Top 5 hottest functions
    top5 = sorted(fn_cumtime.items(), key=lambda x: x[1], reverse=True)[:5]

    suggestions: list[str] = []
    for fn_key, total_ms in top5:
        avg_ms = total_ms / max(fn_calls.get(fn_key, 1), 1)
        suggestions.append(
            f"🔥 {fn_key}  |  total={total_ms:.0f}ms  avg/call={avg_ms:.1f}ms"
            f" — Shadow Candidate: consider @lru_cache or async defer"
        )

    # Check for known bottleneck patterns
    regressions = detect_regressions(samples)
    if regressions:
        suggestions.append("")
        suggestions.append("⚠️  Regressions detected:")
        for r in regressions[:3]:
            suggestions.append(
                f"  • `{r['cmd']}` is {r['delta_pct']}% slower than baseline "
                f"({r['old_ms']}ms → {r['new_ms']}ms). Top culprit: {r['fn']}"
            )

    return suggestions


# ─────────────────────────────────────────────────────────────────────────────
# Convenience decorator
# ─────────────────────────────────────────────────────────────────────────────


def auto_profile(fn: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator: wrap *fn* with the sampling profiler.

    Usage:
        @auto_profile
        def my_slow_function():
            ...
    """
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        return profile_call(fn, *args, **kwargs)

    return wrapper  # type: ignore[return-value]
