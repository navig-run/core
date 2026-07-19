"""tool.py — CLI fallback for gh_cli (spawn-per-call)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import (  # noqa: E402
    cmd_issue_list,
    cmd_pr_create,
    cmd_pr_list,
    cmd_release_list,
    cmd_run,
    cmd_status,
)

TOOL = "gh_cli"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="navig dev gh", description="GitHub CLI wrapper"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prl = sub.add_parser("pr-list", help="List pull requests")
    p_prl.add_argument("--repo", default=None)
    p_prl.add_argument(
        "--state", default="open", choices=["open", "closed", "merged", "all"]
    )
    p_prl.add_argument("--limit", type=int, default=20)
    p_prl.add_argument("--label", default=None)
    p_prl.add_argument("--author", default=None)

    p_prc = sub.add_parser("pr-create", help="Create a pull request")
    p_prc.add_argument("--title", required=True)
    p_prc.add_argument("--body", default="")
    p_prc.add_argument("--base", default="main")
    p_prc.add_argument("--head", default=None)
    p_prc.add_argument("--draft", action="store_true", default=False)
    p_prc.add_argument("--dry-run", action="store_true", default=False)

    p_il = sub.add_parser("issue-list", help="List issues")
    p_il.add_argument("--repo", default=None)
    p_il.add_argument("--state", default="open", choices=["open", "closed", "all"])
    p_il.add_argument("--limit", type=int, default=20)
    p_il.add_argument("--label", default=None)
    p_il.add_argument("--assignee", default=None)

    p_rl = sub.add_parser("release-list", help="List releases")
    p_rl.add_argument("--repo", default=None)
    p_rl.add_argument("--limit", type=int, default=10)

    p_st = sub.add_parser("status", help="Show repo status summary")
    p_st.add_argument("--repo", default=None)

    p_run = sub.add_parser("run", help="List or view workflow runs")
    p_run.add_argument("--repo", default=None)
    p_run.add_argument("--workflow", default=None)
    p_run.add_argument("--limit", type=int, default=10)
    p_run.add_argument("--status", default=None)

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    dispatch = {
        "pr-list": cmd_pr_list,
        "pr-create": cmd_pr_create,
        "issue-list": cmd_issue_list,
        "release-list": cmd_release_list,
        "status": cmd_status,
        "run": cmd_run,
    }

    if command in dispatch:
        result = dispatch[command](params)
    else:
        result = err(TOOL, command, f"Unknown command: {command}", code=1)

    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("ok") else (1 if result.get("errors") else 2))


if __name__ == "__main__":
    main()
