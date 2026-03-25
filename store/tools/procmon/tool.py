"""tool.py — CLI fallback for procmon (spawn-per-call)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_capture, cmd_stop  # noqa: E402

TOOL = "procmon"


def main() -> None:
    parser = argparse.ArgumentParser(prog="navig sys procmon", description="Sysinternals Process Monitor capture")
    sub = parser.add_subparsers(dest="command", required=True)

    p_cap = sub.add_parser("capture", help="Capture events to a PML file")
    p_cap.add_argument("--output", default=None, help="Output .pml path")
    p_cap.add_argument("--duration", type=int, default=15, help="Capture duration in seconds")
    p_cap.add_argument("--filter", default=None, help="PMC filter config path")
    p_cap.add_argument("--dry-run", action="store_true", default=False)

    p_stop = sub.add_parser("stop", help="Terminate any running Procmon instance")

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    dispatch = {
        "capture": cmd_capture,
        "stop": cmd_stop,
    }

    if command in dispatch:
        result = dispatch[command](params)
    else:
        result = err(TOOL, command, f"Unknown command: {command}", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
