"""tool.py — CLI fallback for rclone (spawn-per-call)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import emit, err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_copy, cmd_ls, cmd_remotes, cmd_sync  # noqa: E402

TOOL = "rclone"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="navig cloud rclone", description="rclone cloud sync"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ls = sub.add_parser("ls", help="List remote path")
    p_ls.add_argument("--remote", required=True)
    p_ls.add_argument("--path", default="")
    p_ls.add_argument("--max-depth", type=int, default=1)

    p_sync = sub.add_parser("sync", help="Sync source to dest")
    p_sync.add_argument("--src", required=True)
    p_sync.add_argument("--dst", required=True)
    p_sync.add_argument("--dry-run", action="store_true", default=False)

    p_copy = sub.add_parser("copy", help="Copy source to dest")
    p_copy.add_argument("--src", required=True)
    p_copy.add_argument("--dst", required=True)
    p_copy.add_argument("--dry-run", action="store_true", default=False)

    sub.add_parser("remotes", help="List configured remotes")

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    dispatch = {
        "ls": cmd_ls,
        "sync": cmd_sync,
        "copy": cmd_copy,
        "remotes": cmd_remotes,
    }

    if command in dispatch:
        result = dispatch[command](params)
    else:
        result = err(TOOL, command, f"Unknown command: {command}", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
