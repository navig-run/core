"""Score every MCP tool definition 0–5 and write the evaluation artifact.

    cd core
    python tools/mcp_tdqs.py --json reports/quality/tdqs-evaluation.json

Run it twice for a before/after — the same evaluator, two trees:

    # before: the installed release (no PYTHONPATH), i.e. what the published evaluation saw
    python tools/mcp_tdqs.py --label "installed 3.27.1" --json ../reports/before.json

    # after: this tree
    PYTHONPATH=<repo>/core python tools/mcp_tdqs.py --label "this tree" \
        --json reports/quality/tdqs-evaluation.json

The scoring module is loaded BY PATH (``navig/mcp/definition_score.py`` next to this file),
not imported from the running ``navig``: the "before" side is an installed wheel that has no
such module, and a self-contained scorer is what makes the two runs comparable.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _load_scorer(core_root: Path) -> Any:
    module_path = core_root / "navig" / "mcp" / "definition_score.py"
    spec = importlib.util.spec_from_file_location("navig_definition_score", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover - broken checkout
        raise SystemExit(f"cannot load the scorer from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["navig_definition_score"] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the MCP tool catalogue 0-5 per tool.")
    parser.add_argument("--json", type=Path, help="Write the full evaluation here.")
    parser.add_argument("--label", default="", help="Name this run (e.g. 'installed 3.27.1').")
    parser.add_argument("--worst", type=int, default=8, help="How many lowest scores to print.")
    parser.add_argument(
        "--profile",
        choices=("full", "default"),
        default="full",
        help="full = the legacy catalogue (default); default = the compact routed surface.",
    )
    args = parser.parse_args(argv)

    core_root = Path(__file__).resolve().parents[1]
    scorer = _load_scorer(core_root)

    from navig.mcp_server import MCPProtocolHandler

    try:
        import navig
        import navig.mcp_server as server_module

        version = str(getattr(navig, "__version__", "unknown"))
        module_path = str(server_module.__file__)
    except Exception:  # noqa: BLE001
        version, module_path = "unknown", "unknown"

    handler = MCPProtocolHandler(profile=args.profile)
    tools = handler.advertised_tools() if args.profile != "full" else handler.tools
    report = scorer.score_catalogue(tools)
    report["label"] = args.label or args.profile
    report["profile"] = args.profile
    report["navig_version"] = version
    report["module_path"] = module_path

    print(
        json.dumps(
            {
                "label": args.label,
                "navig_version": version,
                "tools": report["tools"],
                "average": report["average"],
                "target": report["target"],
                "below_target": report["below_target"],
                "families": report["families"],
            },
            indent=2,
        )
    )
    worst = sorted(report["results"], key=lambda row: row["score"])[: max(args.worst, 0)]
    if worst:
        print("\nlowest:")
        for row in worst:
            print(f"  {row['score']:>4}  {row['name']:<40} {', '.join(row['problems'][:3])}")

    if args.json:
        out = args.json if args.json.is_absolute() else (core_root.parent / args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
