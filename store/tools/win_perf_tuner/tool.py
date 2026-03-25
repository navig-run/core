"""tool.py — CLI fallback for win_perf_tuner (spawn-per-call)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_apply, cmd_revert, cmd_scan, cmd_status  # noqa: E402

TOOL = "win_perf_tuner"
TARGETS_HELP = "all | power | kernel | gpu | fx"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="navig sys perf",
        description="Windows performance tuner — power, NTFS cache, kernel, GPU HAGS, visual FX.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Audit all performance settings")
    p_scan.add_argument("--save", default=None, help="Save JSON report to file")

    # status
    sub.add_parser("status", help="Quick one-line status of all tweaks")

    # apply
    p_apply = sub.add_parser("apply", help=f"Apply tweaks. --target {TARGETS_HELP}")
    p_apply.add_argument("--target", default="all", help=TARGETS_HELP)
    p_apply.add_argument("--dry-run", action="store_true", default=False)
    p_apply.add_argument("--yes", action="store_true", default=False)

    # revert
    p_revert = sub.add_parser(
        "revert", help=f"Revert tweaks to Windows defaults. --target {TARGETS_HELP}"
    )
    p_revert.add_argument("--target", default="all", help=TARGETS_HELP)
    p_revert.add_argument("--dry-run", action="store_true", default=False)
    p_revert.add_argument("--yes", action="store_true", default=False)

    args = parser.parse_args()
    params = {k: v for k, v in vars(args).items() if k != "command"}
    # normalise hyphen in dry-run
    if "dry_run" not in params and hasattr(args, "dry_run"):
        params["dry_run"] = args.dry_run

    dispatch = {
        "scan": cmd_scan,
        "status": cmd_status,
        "apply": cmd_apply,
        "revert": cmd_revert,
    }

    handler = dispatch.get(args.command)
    if not handler:
        result = err(TOOL, args.command, ["Unknown subcommand"])
    else:
        result = handler(params)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
