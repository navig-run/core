#!/usr/bin/env python3
"""
NAVIG CLI Performance Benchmarks

Baseline measurements for performance optimization tracking.
Run with: python -m tests.benchmarks.baseline_performance

Baseline Results (Pre-Optimization):
- CLI import time: 280-330ms
- navig --help: 480-640ms
- navig host list: 625-716ms
- navig host use: 484ms
- Config load (16 hosts): 5.1ms
- ConfigManager init: 2.7ms

Target Improvements:
- Command startup: ≥30% reduction (target <500ms)
- Config loading: ≥50% reduction
- SSH operations: 2x faster with pooling
"""

import statistics
import subprocess
import sys
import time

# Number of iterations for each benchmark
ITERATIONS = 5
WARMUP_ITERATIONS = 2


def measure_command(cmd: list[str], iterations: int = ITERATIONS) -> dict:
    """Measure command execution time with statistics."""
    times = []

    # Warmup runs (not counted)
    for _ in range(WARMUP_ITERATIONS):
        subprocess.run(cmd, capture_output=True)

    # Measured runs
    for _ in range(iterations):
        start = time.perf_counter()
        subprocess.run(cmd, capture_output=True)
        elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
        times.append(elapsed)

    return {
        "min": min(times),
        "max": max(times),
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "stdev": statistics.stdev(times) if len(times) > 1 else 0,
        "times": times,
    }


def measure_import(module_name: str, iterations: int = ITERATIONS) -> dict:
    """Measure Python module import time."""
    times = []

    for _ in range(iterations):
        # Use subprocess to get cold import time
        cmd = [
            sys.executable,
            "-c",
            f"import time; start = time.perf_counter(); import {module_name}; print((time.perf_counter() - start) * 1000)",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            times.append(float(result.stdout.strip()))

    if not times:
        return {"error": "Failed to measure import"}

    return {
        "min": min(times),
        "max": max(times),
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "stdev": statistics.stdev(times) if len(times) > 1 else 0,
        "times": times,
    }


def measure_config_operations() -> dict:
    """Measure config loading operations."""
    cmd = [
        sys.executable,
        "-c",
        """
import time

# Measure ConfigManager import
start = time.perf_counter()
from navig.config import ConfigManager
import_time = (time.perf_counter() - start) * 1000

# Measure init
start = time.perf_counter()
cm = ConfigManager()
init_time = (time.perf_counter() - start) * 1000

# Measure list_hosts
start = time.perf_counter()
hosts = cm.list_hosts()
list_time = (time.perf_counter() - start) * 1000

print(f"{import_time:.2f},{init_time:.2f},{list_time:.2f},{len(hosts)}")
""",
    ]

    times = {"import": [], "init": [], "list_hosts": []}
    host_count = 0

    for _ in range(ITERATIONS):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            times["import"].append(float(parts[0]))
            times["init"].append(float(parts[1]))
            times["list_hosts"].append(float(parts[2]))
            host_count = int(parts[3])

    return {
        "import_ms": statistics.mean(times["import"]) if times["import"] else 0,
        "init_ms": statistics.mean(times["init"]) if times["init"] else 0,
        "list_hosts_ms": (
            statistics.mean(times["list_hosts"]) if times["list_hosts"] else 0
        ),
        "host_count": host_count,
    }


def print_result(name: str, result: dict, unit: str = "ms"):
    """Print benchmark result in a formatted way."""
    if "error" in result:
        print(f"  {name}: ERROR - {result['error']}")
        return

    print(f"  {name}:")
    print(f"    Mean:   {result['mean']:.1f} {unit}")
    print(f"    Median: {result['median']:.1f} {unit}")
    print(f"    Min:    {result['min']:.1f} {unit}")
    print(f"    Max:    {result['max']:.1f} {unit}")
    if result.get("stdev"):
        print(f"    StdDev: {result['stdev']:.1f} {unit}")


def run_benchmarks():
    """Run all performance benchmarks."""
    print("=" * 60)
    print("NAVIG CLI Performance Benchmarks")
    print("=" * 60)
    print(f"Iterations per test: {ITERATIONS}")
    print(f"Warmup iterations: {WARMUP_ITERATIONS}")
    print()

    # 1. CLI Import Time
    print("1. CLI Import Time")
    print("-" * 40)
    result = measure_import("navig.cli")
    print_result("navig.cli import", result)
    print()

    # 2. Command Execution Times
    print("2. Command Execution Times")
    print("-" * 40)

    commands = [
        ("navig --help", ["navig", "--help"]),
        ("navig host list --plain", ["navig", "host", "list", "--plain"]),
        ("navig app list --plain", ["navig", "app", "list", "--plain"]),
    ]

    for name, cmd in commands:
        result = measure_command(cmd)
        print_result(name, result)
    print()

    # 3. Config Operations
    print("3. Config Operations")
    print("-" * 40)
    config_result = measure_config_operations()
    print(f"  Config import:  {config_result['import_ms']:.1f} ms")
    print(f"  ConfigManager init: {config_result['init_ms']:.1f} ms")
    print(
        f"  list_hosts ({config_result['host_count']} hosts): {config_result['list_hosts_ms']:.1f} ms"
    )
    print()

    # 4. Individual Module Imports
    print("4. Module Import Times")
    print("-" * 40)
    modules = ["typer", "rich", "yaml", "click"]
    for module in modules:
        result = measure_import(module, iterations=3)
        if "error" not in result:
            print(f"  {module}: {result['mean']:.1f} ms")
    print()

    print("=" * 60)
    print("Benchmark complete")
    print("=" * 60)


if __name__ == "__main__":
    run_benchmarks()
