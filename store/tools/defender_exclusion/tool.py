"""tool.py — CLI fallback for defender_exclusion (spawn-per-call)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_path, cmd_remove, cmd_list, cmd_process  # noqa: E402

TOOL = "defender_exclusion"


def main() -> None:
    parser = argparse.ArgumentParser(prog="navig sys defender", description="Windows Defender exclusion manager")
    sub = parser.add_subparsers(dest="command", required=True)

    p_path = sub.add_parser("exclude", help="Add path/process exclusion")
    sub2 = p_path.add_subparsers(dest="subcommand", required=True)

    p_add = sub2.add_parser("path", help="Exclude a path")
    p_add.add_argument("--path", required=True)
    p_add.add_argument("--dry-run", action="store_true", default=False)

    p_rm = sub2.add_parser("remove", help="Remove a path exclusion")
    p_rm.add_argument("--path", required=True)
    p_rm.add_argument("--dry-run", action="store_true", default=False)

    sub2.add_parser("list", help="List all exclusions")

    p_proc = sub2.add_parser("process", help="Exclude a process by name")
    p_proc.add_argument("--name", required=True)
    p_proc.add_argument("--dry-run", action="store_true", default=False)

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")
    sub_cmd = params.pop("subcommand", None)

    dispatch = {
        "path": cmd_path,
        "remove": cmd_remove,
        "list": cmd_list,
        "process": cmd_process,
    }

    if sub_cmd in dispatch:
        result = dispatch[sub_cmd](params)
    else:
        result = err(TOOL, sub_cmd or command, "Unknown subcommand", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
