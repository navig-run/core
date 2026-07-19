"""tool.py — CLI fallback for nvidia_updater (spawn-per-call)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_check  # noqa: E402

TOOL = "nvidia_updater"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="navig sys nvidia", description="NVIDIA driver update checker"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Check for NVIDIA driver updates")

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    if command == "check":
        result = cmd_check(params)
    else:
        result = err(TOOL, command, f"Unknown command: {command}", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
