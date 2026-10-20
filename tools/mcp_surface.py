"""Generate the compact MCP surface contract and its full-vs-compact comparison.

    cd core
    python tools/mcp_surface.py                      # dry summary
    python tools/mcp_surface.py --json ../reports/quality/compact-surface.json \
        --markdown ../reports/quality/compact-surface.md

The matrix is derived from the same ``ROUTER_SPECS`` the server registers, so the
published contract cannot drift from the running surface. Every row names the original
handler, its module, safety level, platform availability, parameters, annotations and
authorisation requirement.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_PROVIDER_TOOL = re.compile(r"^connector_(.+)_(search|fetch|act)$")


def _git_rev() -> str:
    try:
        core_root = Path(__file__).resolve().parents[1]
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=core_root,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()
    except Exception:  # noqa: BLE001 - an artifact without a revision is still evidence
        return "unknown"


def _module_of(handler: Any) -> str:
    return str(getattr(handler, "__module__", "unknown"))


def _rows(full: Any, compact: Any) -> list[dict[str, Any]]:
    from navig.mcp.routing import ROUTER_SPECS

    rows: list[dict[str, Any]] = []
    for spec in ROUTER_SPECS.values():
        annotations = compact.tools[spec.name]["annotations"]
        for action in spec.actions:
            original_name = action.original
            if action.connector:
                providers = sorted(
                    match.group(1)
                    for name in full._tool_handlers
                    if (match := _PROVIDER_TOOL.match(name)) and match.group(2) == action.connector
                )
                original_name = f"connector_<provider>_{action.connector}"
                schema = None
                safety = "dangerous" if action.connector == "act" else "moderate"
                module = "navig.mcp.tools.connectors"
            else:
                tool = full.tools.get(action.original or "") or {}
                schema = tool.get("inputSchema") or {}
                safety = full._tool_safety.get(action.original or "", "safe")
                module = _module_of(full._tool_handlers.get(action.original or ""))
                providers = []
            rows.append(
                {
                    "router": spec.name,
                    "action": action.action,
                    "original": original_name,
                    "module": module,
                    "platform": "all",
                    "safety": safety,
                    "required": (schema or {}).get("required") or [],
                    "optional": sorted(
                        set((schema or {}).get("properties") or {})
                        - set((schema or {}).get("required") or [])
                    ),
                    "router_annotations": annotations,
                    "authorization": (
                        "read-only; no approval"
                        if spec.kind == "read" and safety == "safe"
                        else "approval gate (moderate: audited)"
                        if safety == "moderate"
                        else "approval gate (dangerous); denied over remote unless mcp.remote_dangerous=approve"
                    ),
                    "providers": providers,
                }
            )
    return rows


def build() -> dict[str, Any]:
    from navig.mcp.definition_score import score_catalogue
    from navig.mcp.routing import ROUTER_SPECS
    from navig.mcp_server import MCPProtocolHandler

    full = MCPProtocolHandler()
    compact = MCPProtocolHandler(profile="default")
    rows = _rows(full, compact)
    routed = {
        row["original"] for row in rows if not row["original"].startswith("connector_<provider>")
    }
    # Connector reads/writes are routed dynamically by provider; concrete connector_*
    # tools are reachable through navig_connectors / navig_connectors_act, not full-only.
    routed |= {"connector_list", "connector_health"}
    routed |= {name for name in full._tool_handlers if _PROVIDER_TOOL.match(name)}
    full_names = sorted(full.advertised_tools())
    compact_names = sorted(compact.advertised_tools())
    full_bytes = sum(len(json.dumps(t)) for t in full.advertised_tools().values())
    compact_bytes = sum(len(json.dumps(t)) for t in compact.advertised_tools().values())
    score = score_catalogue(compact.advertised_tools())
    return {
        "generated_from": _git_rev(),
        "platform": sys.platform,
        "profiles": {
            "default": {"tools": len(compact_names), "names": compact_names},
            "full": {"tools": len(full_names)},
        },
        "full_only": sorted(set(full_names) - routed - set(compact_names)),
        "definition_bytes": {"compact": compact_bytes, "full": full_bytes},
        "token_estimate": {"compact": compact_bytes // 4, "full": full_bytes // 4},
        "compact_tdqs": {
            "average": score["average"],
            "below_target": score["below_target"],
            "target": score["target"],
        },
        "routers": {
            name: {"kind": spec.kind, "actions": len(spec.actions)}
            for name, spec in ROUTER_SPECS.items()
        },
        "rows": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Compact MCP surface contract",
        "",
        "Generated by `core/tools/mcp_surface.py` from `navig/mcp/routing.py`; the table "
        "below is the surface the server actually registers.",
        "",
        f"* default profile: **{report['profiles']['default']['tools']} tools**",
        f"* full profile: **{report['profiles']['full']['tools']} tools** (platform-dependent)",
        f"* definition payload: {report['definition_bytes']['compact']:,} B compact vs "
        f"{report['definition_bytes']['full']:,} B full "
        f"(~{report['token_estimate']['compact']:,} vs ~{report['token_estimate']['full']:,} tokens)",
        f"* compact TDQS (local scorer): **{report['compact_tdqs']['average']}/5**",
        "",
        "| Router | Action | Original handler | Safety | Required | Optional | Authorisation |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| `{row['router']}` | `{row['action']}` | `{row['original']}` | "
            f"{row['safety']} | {', '.join(row['required']) or '—'} | "
            f"{', '.join(row['optional']) or '—'} | {row['authorization']} |"
        )
    lines += [
        "",
        "## Full-profile-only operations",
        "",
        "Every handler stays registered in both profiles; the compact surface maps the "
        "domains above and the rest remain reachable with `--profile full`:",
        "",
        "```",
        *report["full_only"],
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the compact MCP surface contract.")
    parser.add_argument(
        "--json", type=Path, help="Write the matrix JSON here (repo-root relative)."
    )
    parser.add_argument(
        "--markdown", type=Path, help="Write the human table here (repo-root relative)."
    )
    args = parser.parse_args(argv)
    report = build()
    print(
        f"compact {report['profiles']['default']['tools']} tools, "
        f"full {report['profiles']['full']['tools']} tools, "
        f"{len(report['rows'])} actions, tdqs {report['compact_tdqs']['average']}"
    )
    core_root = Path(__file__).resolve().parents[1]
    root = core_root.parent
    if args.json:
        out = args.json if args.json.is_absolute() else root / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    if args.markdown:
        out = args.markdown if args.markdown.is_absolute() else root / args.markdown
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(report), encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
