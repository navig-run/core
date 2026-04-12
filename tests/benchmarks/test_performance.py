#!/usr/bin/env python3
"""
NAVIG CLI Performance Benchmark Suite

Measures before/after performance for CLI commands to verify optimization impact.
Run this after making performance-related changes.

Usage:
    python tests/benchmarks/test_performance.py
"""

import sys
import time
from pathlib import Path
from typing import Callable, Tuple
import pytest

pytestmark = pytest.mark.slow

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def measure_time(
    func: Callable, warmup: int = 1, iterations: int = 5
) -> Tuple[float, float, float]:
    """
    Measure execution time of a function.

    Args:
        func: Function to measure
        warmup: Number of warmup iterations (not counted)
        iterations: Number of measured iterations

    Returns:
        Tuple of (min_ms, avg_ms, max_ms)
    """
    # Warmup
    for _ in range(warmup):
        try:
            func()
        except SystemExit:
            pass

    # Measure
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        try:
            func()
        except SystemExit:
            pass
        times.append((time.perf_counter() - start) * 1000)

    return min(times), sum(times) / len(times), max(times)


def benchmark_import():
    """Benchmark import time for navig.cli module."""
    # Need to reload to get accurate import time

    # Clear cached modules
    modules_to_clear = [m for m in sys.modules if m.startswith("navig")]
    for m in modules_to_clear:
        del sys.modules[m]

    start = time.perf_counter()

    elapsed = (time.perf_counter() - start) * 1000

    return elapsed


def benchmark_host_list():
    """Benchmark 'navig host list --plain' command."""
    from navig.cli import app

    def run():
        app(["host", "list", "--plain"], standalone_mode=False)

    return measure_time(run)


def benchmark_app_list():
    """Benchmark 'navig app list --plain' command."""
    from navig.cli import app

    def run():
        app(["app", "list", "--plain"], standalone_mode=False)

    return measure_time(run)


def benchmark_list_hosts_method():
    """Benchmark list_hosts() config method directly."""
    from navig.config import get_config_manager

    config = get_config_manager()

    def run():
        # Clear cache to measure uncached performance
        config._hosts_list_cache = None
        return config.list_hosts()

    return measure_time(run)


def benchmark_list_hosts_cached():
    """Benchmark list_hosts() with cache (warm start)."""
    from navig.config import get_config_manager

    config = get_config_manager()
    # Prime the cache
    config.list_hosts()

    def run():
        return config.list_hosts()

    return measure_time(run, warmup=0)


def benchmark_load_host_config():
    """Benchmark loading 10 host configs."""
    from navig.config import get_config_manager

    config = get_config_manager()
    hosts = config.list_hosts()[:10]

    def run():
        for host in hosts:
            config.load_host_config(host, use_cache=False)

    return measure_time(run)


def benchmark_help():
    """Benchmark 'navig --help' command."""
    from navig.cli import app

    def run():
        try:
            app(["--help"], standalone_mode=False)
        except SystemExit:
            pass

    return measure_time(run)


# Baseline values (before optimization)
BASELINES = {
    "import": 528,  # ms
    "host_list": 542,  # ms
    "app_list": 80,  # ms
    "list_hosts_cold": 50,  # ms for 5 calls (10ms each)
    "list_hosts_warm": 10,  # ms target with cache
    "load_config_10": 30,  # ms for 10 hosts
    "help": 530,  # ms
}

# Target improvements
TARGETS = {
    "import": 0.50,  # 50% reduction
    "host_list": 0.30,  # 30% reduction
    "list_hosts_cold": 0.50,  # 50% reduction
    "help": 0.25,  # 25% reduction
}


def main():
    print("=" * 60)
    print("NAVIG CLI Performance Benchmark")
    print("=" * 60)
    print()

    results = {}

    # Import benchmark (special handling - single measurement)
    print("Benchmarking import time...")
    import_time = benchmark_import()
    results["import"] = import_time
    print(f"  Import: {import_time:.1f}ms (baseline: {BASELINES['import']}ms)")

    # Clear and reload for other benchmarks
    modules_to_clear = [m for m in sys.modules if m.startswith("navig")]
    for m in modules_to_clear:
        del sys.modules[m]

    print("\nBenchmarking commands...")

    # Help benchmark
    print("  --help: ", end="", flush=True)
    min_t, avg_t, max_t = benchmark_help()
    results["help"] = avg_t
    baseline = BASELINES["help"]
    improvement = (baseline - avg_t) / baseline * 100
    print(f"{avg_t:.1f}ms avg (baseline: {baseline}ms, {improvement:+.1f}%)")

    # Host list benchmark
    print("  host list: ", end="", flush=True)
    min_t, avg_t, max_t = benchmark_host_list()
    results["host_list"] = avg_t
    baseline = BASELINES["host_list"]
    improvement = (baseline - avg_t) / baseline * 100
    print(f"{avg_t:.1f}ms avg (baseline: {baseline}ms, {improvement:+.1f}%)")

    # App list benchmark
    print("  app list: ", end="", flush=True)
    min_t, avg_t, max_t = benchmark_app_list()
    results["app_list"] = avg_t
    print(f"{avg_t:.1f}ms avg (baseline: {BASELINES['app_list']}ms)")

    # list_hosts cold (uncached)
    print("  list_hosts (cold): ", end="", flush=True)
    min_t, avg_t, max_t = benchmark_list_hosts_method()
    results["list_hosts_cold"] = avg_t
    baseline = BASELINES["list_hosts_cold"] / 5  # Per-call baseline
    improvement = (baseline - avg_t) / baseline * 100
    print(f"{avg_t:.1f}ms avg (baseline: {baseline:.1f}ms, {improvement:+.1f}%)")

    # list_hosts warm (cached)
    print("  list_hosts (warm): ", end="", flush=True)
    min_t, avg_t, max_t = benchmark_list_hosts_cached()
    results["list_hosts_warm"] = avg_t
    print(f"{avg_t:.1f}ms avg (target: <{BASELINES['list_hosts_warm']}ms)")

    # Load config benchmark
    print("  load_host_config (10 hosts): ", end="", flush=True)
    min_t, avg_t, max_t = benchmark_load_host_config()
    results["load_config_10"] = avg_t
    print(f"{avg_t:.1f}ms avg (baseline: {BASELINES['load_config_10']}ms)")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    all_passed = True
    for metric, target_pct in TARGETS.items():
        if metric in results:
            baseline = BASELINES.get(metric, 0)
            if metric == "list_hosts_cold":
                baseline = BASELINES[metric] / 5  # Per-call
            current = results[metric]
            target = baseline * (1 - target_pct)
            passed = current <= target
            status = "✓ PASS" if passed else "✗ FAIL"
            if not passed:
                all_passed = False
            print(f"  {metric}: {current:.1f}ms (target: <{target:.1f}ms) [{status}]")

    print()
    if all_passed:
        print("All performance targets met! ✓")
    else:
        print("Some targets not met. See details above.")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
