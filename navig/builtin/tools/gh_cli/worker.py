"""
gh_cli worker — GitHub CLI wrapper.
CLI path: dev gh pr list | pr create | issue list | release list | status | run
"""

from __future__ import annotations

import json
import shlex
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "_lib"))
from common import Timer, emit, err, ok, require_on_path, run

TOOL = "gh_cli"


def _gh():
    return require_on_path("gh")


def _run_gh(
    subcmd: list[str],
    extra_flags: list[str] | None = None,
    dry: bool = False,
    timeout: int = 60,
) -> tuple[int, str, str]:
    return run(
        [
            _gh(),
            *subcmd,
            "--json",
            "number,title,state,url,author,createdAt",
            *(extra_flags or []),
        ],
        timeout=timeout,
        dry_run=dry,
    )


def _parse_json_or_raw(out: str) -> list | dict:
    try:
        return json.loads(out)
    except Exception:
        return {"raw": out}


def cmd_pr_list(args: dict) -> dict:
    t = Timer()
    try:
        cmd = [
            "pr",
            "list",
            "--limit",
            str(args.get("limit", 20)),
            "--state",
            args.get("state", "open"),
        ]
        if args.get("repo"):
            cmd += ["--repo", args["repo"]]
        rc, out, er = run(
            [_gh(), *cmd, "--json", "number,title,state,url,author,createdAt"],
            timeout=30,
        )
        if rc != 0:
            return err(TOOL, "pr-list", [er or out], ms=t.ms())
        return ok(TOOL, "pr-list", {"prs": _parse_json_or_raw(out)}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "pr-list", [str(e)], ms=t.ms())


def cmd_pr_create(args: dict) -> dict:
    t = Timer()
    try:
        cmd = [
            "pr",
            "create",
            "--title",
            args["title"],
            "--body",
            args.get("body", ""),
            "--base",
            args.get("base", "main"),
        ]
        if args.get("draft"):
            cmd.append("--draft")
        rc, out, er = run([_gh(), *cmd], dry_run=args.get("dry_run", False), timeout=30)
        if rc != 0 and not args.get("dry_run"):
            return err(TOOL, "pr-create", [er or out], ms=t.ms())
        return ok(
            TOOL,
            "pr-create",
            {"output": out, "dry_run": args.get("dry_run", False)},
            ms=t.ms(),
        )
    except Exception as e:
        return err(TOOL, "pr-create", [str(e)], ms=t.ms())


def cmd_issue_list(args: dict) -> dict:
    t = Timer()
    try:
        cmd = [
            "issue",
            "list",
            "--limit",
            str(args.get("limit", 20)),
            "--state",
            args.get("state", "open"),
        ]
        if args.get("repo"):
            cmd += ["--repo", args["repo"]]
        if args.get("label"):
            cmd += ["--label", args["label"]]
        rc, out, er = run(
            [_gh(), *cmd, "--json", "number,title,state,url,author,createdAt,labels"],
            timeout=30,
        )
        if rc != 0:
            return err(TOOL, "issue-list", [er or out], ms=t.ms())
        return ok(TOOL, "issue-list", {"issues": _parse_json_or_raw(out)}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "issue-list", [str(e)], ms=t.ms())


def cmd_release_list(args: dict) -> dict:
    t = Timer()
    try:
        cmd = ["release", "list", "--limit", str(args.get("limit", 10))]
        if args.get("repo"):
            cmd += ["--repo", args["repo"]]
        rc, out, er = run([_gh(), *cmd], timeout=30)
        if rc != 0:
            return err(TOOL, "release-list", [er or out], ms=t.ms())
        releases = [{"raw": line} for line in out.splitlines() if line.strip()]
        return ok(TOOL, "release-list", {"releases": releases}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "release-list", [str(e)], ms=t.ms())


def cmd_status(args: dict) -> dict:
    t = Timer()
    try:
        rc, out, er = run([_gh(), "status"], timeout=30)
        if rc != 0:
            return err(TOOL, "status", [er or out], ms=t.ms())
        return ok(TOOL, "status", {"output": out}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "status", [str(e)], ms=t.ms())


def cmd_run(args: dict) -> dict:
    t = Timer()
    try:
        raw_args = shlex.split(args["args"])
        rc, out, er = run([_gh(), *raw_args], timeout=60)
        return ok(TOOL, "run", {"output": out, "stderr": er, "rc": rc}, ms=t.ms())
    except Exception as e:
        return err(TOOL, "run", [str(e)], ms=t.ms())


HANDLERS = {
    "pr-list": cmd_pr_list,
    "pr-create": cmd_pr_create,
    "issue-list": cmd_issue_list,
    "release-list": cmd_release_list,
    "status": cmd_status,
    "run": cmd_run,
}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            handler = HANDLERS.get(req.get("command", ""))
            emit(
                handler(req.get("args", {}))
                if handler
                else err(TOOL, req.get("command", "?"), ["Unknown command"])
            )
        except Exception as e:
            emit(err(TOOL, "?", [str(e)]))


if __name__ == "__main__":
    main()
