"""tool.py — CLI fallback for futurerestore (spawn-per-call)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_restore  # noqa: E402

TOOL = "futurerestore"


def main() -> None:
    parser = argparse.ArgumentParser(prog="navig ios futurerestore", description="futurerestore iOS OTA restorer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_restore = sub.add_parser("restore", help="Restore iOS device")
    p_restore.add_argument("--blob", required=True, help="Path to .shsh2 blob")
    p_restore.add_argument("--ipsw", required=True, help="Path to .ipsw firmware")
    p_restore.add_argument("--latest-sep", action="store_true", default=False)
    p_restore.add_argument("--latest-baseband", action="store_true", default=False)
    p_restore.add_argument("--no-baseband", action="store_true", default=False)
    p_restore.add_argument("--dry-run", action="store_true", default=False)

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    if command == "restore":
        result = cmd_restore(params)
    else:
        result = err(TOOL, command, f"Unknown command: {command}", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
