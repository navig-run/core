"""tool.py — CLI fallback for nssm (spawn-per-call)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import (  # noqa: E402
    cmd_install,
    cmd_remove,
    cmd_start,
    cmd_status,
    cmd_stop,
)

TOOL = "nssm"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="navig sys nssm", description="NSSM Windows service manager"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_install = sub.add_parser("install", help="Install a service")
    p_install.add_argument("--name", required=True)
    p_install.add_argument("--exe", required=True)
    p_install.add_argument("--args", default="")
    p_install.add_argument("--display", default=None)
    p_install.add_argument("--dry-run", action="store_true", default=False)

    p_start = sub.add_parser("start", help="Start a service")
    p_start.add_argument("--name", required=True)

    p_stop = sub.add_parser("stop", help="Stop a service")
    p_stop.add_argument("--name", required=True)

    p_remove = sub.add_parser("remove", help="Remove a service")
    p_remove.add_argument("--name", required=True)
    p_remove.add_argument("--yes", action="store_true", default=False)
    p_remove.add_argument("--dry-run", action="store_true", default=False)

    p_status = sub.add_parser("status", help="Query service status")
    p_status.add_argument("--name", required=True)

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    dispatch = {
        "install": cmd_install,
        "start": cmd_start,
        "stop": cmd_stop,
        "remove": cmd_remove,
        "status": cmd_status,
    }

    if command in dispatch:
        result = dispatch[command](params)
    else:
        result = err(TOOL, command, f"Unknown command: {command}", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
