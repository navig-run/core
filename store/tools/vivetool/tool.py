"""tool.py — CLI fallback for vivetool (spawn-per-call)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_enable, cmd_disable, cmd_query  # noqa: E402

TOOL = "vivetool"


def main() -> None:
    parser = argparse.ArgumentParser(prog="navig sys vivetool", description="ViVeTool Windows feature flags")
    sub = parser.add_subparsers(dest="command", required=True)

    p_enable = sub.add_parser("enable", help="Enable feature IDs")
    p_enable.add_argument("--ids", required=True, help="Comma-separated feature IDs")
    p_enable.add_argument("--dry-run", action="store_true", default=False)

    p_disable = sub.add_parser("disable", help="Disable feature IDs")
    p_disable.add_argument("--ids", required=True, help="Comma-separated feature IDs")
    p_disable.add_argument("--dry-run", action="store_true", default=False)

    p_query = sub.add_parser("query", help="Query feature ID state")
    p_query.add_argument("--id", required=True, help="Feature ID to query")

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    dispatch = {
        "enable": cmd_enable,
        "disable": cmd_disable,
        "query": cmd_query,
    }

    if command in dispatch:
        result = dispatch[command](params)
    else:
        result = err(TOOL, command, f"Unknown command: {command}", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
