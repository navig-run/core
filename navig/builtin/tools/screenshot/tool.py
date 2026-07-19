"""tool.py — CLI fallback for screenshot (spawn-per-call)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_monitors, cmd_take  # noqa: E402

TOOL = "screenshot"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="navig sys screenshot", description="Cross-platform screenshot capture"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_take = sub.add_parser("take", help="Capture a screenshot")
    p_take.add_argument("--output", default=None, help="Output file path (.png)")
    p_take.add_argument("--monitor", type=int, default=0, help="Monitor index (0=all)")
    p_take.add_argument("--region", default=None, help="Crop region as x,y,w,h")
    p_take.add_argument("--quality", type=int, default=95, help="JPEG quality (1-100)")
    p_take.add_argument("--dry-run", action="store_true", default=False)

    sub.add_parser("monitors", help="List available monitors")

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    dispatch = {
        "take": cmd_take,
        "monitors": cmd_monitors,
    }

    if command in dispatch:
        result = dispatch[command](params)
    else:
        result = err(TOOL, command, f"Unknown command: {command}", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
