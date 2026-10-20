"""Dump the MCP catalogue as an auditable matrix.

    cd core
    python tools/mcp_inventory.py --out reports/quality/mcp-inventory.json

The invariants live in ``tests/quality/`` — this file is the census, not a guard. It
answers "what does the server actually advertise, with which metadata", which is the input
to every audit batch and the before/after column of the quality report. The signals
themselves are shared with the regression budget (``navig.mcp.catalogue_audit``), so the
number this prints and the number a test enforces cannot drift apart.

Why it constructs a real ``MCPProtocolHandler`` instead of reading the bundle files: a
tool's definition is not its source. Titles, annotations and schema strictness are applied
once, after every bundle registers (``navig/mcp/annotations.annotate_tools``), and the
connector tools are generated at import time from each connector's manifest. Reading the
files would miss exactly the drift this matrix exists to catch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _git_rev(repo_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            # git's contract is UTF-8; text=True alone decodes with the machine's locale
            # (cp1251 here) and silently corrupts a non-ASCII subject. The gate refuses
            # the omission — tests/quality/test_git_subprocess_encoding.py.
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 - a missing git is not a reason to fail the census
        return "unknown"


def build_matrix(repo_root: Path) -> dict[str, Any]:
    from navig.mcp.catalogue_audit import counts, signals
    from navig.mcp_server import MCPProtocolHandler

    handler = MCPProtocolHandler()
    handlers = getattr(handler, "_tool_handlers", {})
    rows = {
        name: {
            "name": name,
            "family": name.split("_", 1)[0],
            "handler_registered": name in handlers,
            **signals(tool),
        }
        for name, tool in sorted(handler.tools.items())
    }
    family_totals: dict[str, int] = {}
    for row in rows.values():
        family_totals[row["family"]] = family_totals.get(row["family"], 0) + 1

    return {
        "generated_from": _git_rev(repo_root),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capabilities": sorted(handler._handle_initialize({})["capabilities"]),
        "counts": {
            **counts(rows),
            "resources": len(handler.resources),
            "prompts": len(handler.prompts),
            "no_handler": sum(1 for row in rows.values() if not row["handler_registered"]),
            "families": dict(sorted(family_totals.items())),
        },
        "resources": sorted(handler.resources),
        "tools": list(rows.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dump the MCP catalogue as JSON.")
    parser.add_argument(
        "--out",
        type=Path,
        help="Write the matrix here (default: stdout). Relative paths resolve against the repo root.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    matrix = build_matrix(repo_root)
    payload = json.dumps(matrix, indent=2, ensure_ascii=False) + "\n"

    if args.out is None:
        sys.stdout.write(payload)
        return 0

    out = args.out if args.out.is_absolute() else (repo_root / args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8")
    totals = matrix["counts"]
    print(
        f"wrote {out} — {totals['tools']} tools, {totals['resources']} resources, "
        f"{totals['prompts']} prompts, {totals['no_handler']} without handler"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
