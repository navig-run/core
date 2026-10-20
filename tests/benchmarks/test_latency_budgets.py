"""Latency budgets for the paths a live UI rides: CLI spawn, process listing, gateway.

Why these budgets exist (measured 2026-10-15 on the operator box, values in the
assert messages are from the run that executes them):

  * A process-manager UI in NAVIG OS has exactly two ways to get data — spawn a
    `navig` subprocess, or call the gateway HTTP API. The numbers below pin the
    CLI side; `test_gateway_latency.py` pins the HTTP side. They differ by an
    order of magnitude (~230 ms `--help` / ~0.9 s real command / ~2.3 s
    `navig procs` versus ~0.4–15 ms per gateway request), which is why the
    intended pattern is a gateway route, not per-action subprocess spawns.
  * The process walk is the process manager's core operation. Measured: a
    filtered walk (`find_processes_by_group("node")`) is ~30–60 ms, while the
    full table (`_to_info` over every process) is ~3.2 s for 556 processes —
    `cmdline()` alone costs ~5.5 ms/proc (~3.1 s). Per-attribute profile:
    name 0.01 ms, memory_info 0.04 ms, create_time 0.06 ms, username 0.08 ms,
    cwd 1.8 ms, cmdline 5.5 ms, status ~28 ms/proc (!). A regression that adds
    `status()`/`ppid()` to the full walk turns 3.2 s into ~19 s; these budgets
    are the guard.

Opt-in: set ``NAVIG_TEST_PERF=1``. A wall-clock budget is only meaningful on a
quiet box. Under the land gate's parallel workers the same `navig --help` measured
4.1 s and `navig config get` 16.2 s (vs 0.23 s / 0.9 s idle), and the gate's
changed-modules selection runs a changed test file regardless of the `slow`
marker, so the marker alone did not keep them out. Run them deliberately:

    NAVIG_TEST_PERF=1 py -3.13 -m pytest --no-cov tests/benchmarks/test_latency_budgets.py

Budgets carry 3–10x headroom over the measured idle baseline; the assertion
messages print the observed numbers so a failure is a measurement, not a vibe.
"""

import os
import statistics
import subprocess
import sys
import time

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("NAVIG_TEST_PERF") != "1",
        reason="wall-clock perf budget — set NAVIG_TEST_PERF=1 on a quiet box to run",
    ),
]


def _cli_ms(args: list[str], timeout: float = 60.0) -> tuple[int, float]:
    """Run `python -m navig <args>` cold and return (returncode, elapsed ms)."""
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "navig", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, (time.perf_counter() - t0) * 1000


def test_cli_help_cold_start_budget():
    """`navig --help` must stay a glanceable command (measured ~0.23 s)."""
    runs = []
    for _ in range(2):
        rc, ms = _cli_ms(["--help"])
        assert rc == 0, f"navig --help exited {rc}"
        runs.append(ms)
    med = statistics.median(runs)
    assert med < 3000, (
        f"navig --help median {med:.0f} ms exceeds the 3000 ms budget "
        f"(runs: {[round(r) for r in runs]} ms; baseline ~230 ms — lazy-import "
        f"regression? compare `python -X importtime -m navig --help | tail`)"
    )


def test_cli_config_read_cold_start_budget():
    """A real (config-loading) command: measured ~0.9 s, budget 5 s."""
    rc, ms = _cli_ms(["config", "get", "gateway.host"])
    assert rc == 0, f"navig config get exited {rc}"
    assert ms < 5000, (
        f"navig config get took {ms:.0f} ms, budget 5000 ms (baseline ~900 ms). "
        f"Config/vault/plugin discovery grew?"
    )


def test_filtered_process_listing_budget():
    """The process manager's filtered walk (what `navig procs node` does in-proc)."""
    from navig.commands import processes as pc

    runs = []
    for _ in range(3):
        t0 = time.perf_counter()
        rows = pc.describe_processes(pc.find_processes_by_group("node"))
        runs.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(runs)
    assert med < 1500, (
        f"filtered process walk median {med:.0f} ms exceeds the 1500 ms budget "
        f"(runs: {[round(r) for r in runs]} ms, {len(rows)} rows; baseline "
        f"~30–60 ms — per-proc work crept into the finders?)"
    )


def test_full_process_walk_budget():
    """The full-table walk a 'list all processes' endpoint would run.

    Guards the measured 3.2 s baseline (556 procs, cmdline-dominated) against
    per-attribute regressions: adding `status()` (+~16 s) or `ppid()` would
    blow this 10 s budget immediately.
    """
    import psutil

    from navig.commands import processes as pc

    t0 = time.perf_counter()
    procs = list(psutil.process_iter())
    t_scan = time.perf_counter()
    rows = pc.describe_processes([pc._to_info(p) for p in procs])
    total_ms = (time.perf_counter() - t0) * 1000
    scan_ms = (t_scan - t0) * 1000
    walk_ms = total_ms - scan_ms
    n = len(procs)
    per_proc = walk_ms / max(n, 1)
    assert total_ms < 10_000, (
        f"full process walk {total_ms:.0f} ms for {n} procs "
        f"(scan {scan_ms:.0f} ms + to_info/describe {walk_ms:.0f} ms, "
        f"{per_proc:.2f} ms/proc) exceeds the 10000 ms budget; baseline "
        f"~3200 ms / 556 procs / ~5.7 ms per proc (cmdline dominates). "
        f"Did a high-cost attribute (status ~28 ms/proc, ppid, cwd) enter _to_info?"
    )
    assert rows, f"walk produced no rows from {n} processes — the budget passed on an empty result"
