"""Tool-selection benchmark over the MCP catalogue — CLI.

    cd core
    python tools/mcp_selection_benchmark.py --json reports/quality/selection-benchmark.json
    python tools/mcp_selection_benchmark.py --dump-misses

The scoring lives in ``navig/mcp/selection_benchmark.py`` so the report and the CI floor
(``tests/quality/test_mcp_selection_benchmark.py``) compute the same number.

Before/after, without leaving the machine:

    # after (this checkout)
    PYTHONPATH=<repo>/core python tools/mcp_selection_benchmark.py --json after.json
    # before (an installed release: no PYTHONPATH, so the wheel is imported)
    python tools/mcp_selection_benchmark.py --json before.json

The catalogues differ between versions; the report records the tool count, the distribution
version AND the module path actually imported, so a comparison cannot pretend otherwise.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lexical tool-selection benchmark for the MCP catalogue.")
    parser.add_argument("--json", type=Path, help="Write the full report here.")
    parser.add_argument("--dump-misses", action="store_true", help="Print the requests that missed.")
    args = parser.parse_args(argv)

    from navig.mcp.selection_benchmark import run
    from navig.mcp_server import MCPProtocolHandler

    try:
        import navig
        import navig.mcp_server as _server

        version = str(getattr(navig, "__version__", "unknown"))
        module_path = str(_server.__file__)
    except Exception:  # noqa: BLE001
        version, module_path = "unknown", "unknown"

    report = run(MCPProtocolHandler().tools)
    # `navig.__version__` comes from the INSTALLED distribution metadata, which can be older
    # than the source being ranked — recording the module path keeps before/after honest.
    report["navig_version"] = version
    report["module_path"] = module_path

    if args.dump_misses:
        for row in report["results"]:
            if row.get("skipped") or row["top1"]:
                continue
            print(
                f"MISS {row['request']!r} -> picked {row['picked']} "
                f"(expected {row['expected']}, rank {row['rank_of_expected']})"
            )

    summary_keys = (
        "tool_count",
        "requests",
        "scored",
        "top1",
        "acceptable_top1",
        "top3",
        "accuracy_top1",
        "accuracy_acceptable_top1",
        "accuracy_top3",
    )
    print(json.dumps({"navig_version": version, **{key: report[key] for key in summary_keys}}, indent=2))

    if args.json:
        out = args.json if args.json.is_absolute() else (Path(__file__).resolve().parents[2] / args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
