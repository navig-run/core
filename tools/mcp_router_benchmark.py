"""Run the compact-surface tool+action selection benchmark.

    cd core
    python tools/mcp_router_benchmark.py --json ../reports/quality/compact-surface-benchmark.json

Deterministic BM25 over the definitions a client receives — no key, no model, no drift.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tool+action benchmark for the compact MCP surface."
    )
    parser.add_argument(
        "--json", type=Path, help="Write the full report here (repo-root relative)."
    )
    parser.add_argument("--dump-misses", action="store_true", help="Print requests that missed.")
    args = parser.parse_args(argv)

    from navig.mcp.router_benchmark import BENCHMARK, run
    from navig.mcp.routing import ROUTER_SPECS

    report = run()
    print(
        json.dumps(
            {
                "requests": report["requests"],
                "tool_top1": report["tool_top1"],
                "action_top1": report["action_top1"],
                "tool_top3": report["tool_top3"],
                "accuracy_tool_top1": report["accuracy_tool_top1"],
                "accuracy_action_top1": report["accuracy_action_top1"],
                "accuracy_tool_top3": report["accuracy_tool_top3"],
            },
            indent=2,
        )
    )
    if args.dump_misses:
        for row in report["results"]:
            if not row["action_correct"]:
                print(
                    f"MISS {row['request']!r} -> {row['picked']} (expected {row['expected']}, rank {row['rank']})"
                )

    if args.json:
        core_root = Path(__file__).resolve().parents[1]
        out = args.json if args.json.is_absolute() else (core_root.parent / args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        report["catalogue"] = {
            "tools": len(ROUTER_SPECS),
            "actions": sum(len(spec.actions) for spec in ROUTER_SPECS.values()),
        }
        report["requests_total"] = len(BENCHMARK)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
