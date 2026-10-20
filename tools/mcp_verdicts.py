"""Give every advertised MCP tool an explicit audit verdict, with the evidence named.

    cd core
    python tools/mcp_verdicts.py                    # writes reports/quality/verdict-matrix.json

Inputs are the census (``reports/quality/mcp-inventory.json``) and the test tree — nothing
live is executed, because the matrix has to be true on a laptop, in CI, and in a container.
A verdict is only as strong as its evidence, so each row says which evidence it used:

``PASS``       structural checks pass AND a test drives the tool (a ``tools/call`` payload,
               a ``_tool_handlers[...]`` access, or a direct ``_tool_<name>`` call).
``PARTIAL``    structural checks pass, no test drives it — the behaviour is known by
               inspection only, and this file says so instead of calling it verified.
``BLOCKED``    needs a live third-party account/service (connector, Firecrawl, Perplexity,
               a browser) that this audit does not own. Unit evidence may still exist; the
               ``live_check`` field records what was NOT attempted.
``UNSUPPORTED`` the deployment platform cannot run it. The matrix is computed for the
               supplied deployment (Linux container), so the Windows-only UI tools are
               listed here — and since the platform gate they are no longer advertised
               either, which is the stronger fix.
``FAIL``       a structural check fails (no handler, loose schema, missing annotation).

The point is the one the mission asks for: no tool may be left in "probably fine".
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: The supplied deployment: Debian container running `navig mcp serve`.
DEPLOYMENT_PLATFORM = "linux"

#: Tools whose live behaviour needs an account or service this audit does not own.
_EXTERNAL_PREFIXES = ("connector_", "firecrawl_", "perplexity_", "dock_")
_EXTERNAL_TOOLS = frozenset({"navig_web_search", "navig_bay_acquire", "navig_block_apply"})

#: Windows UI-automation tools (the `desktop` bundle). `desktop_filesystem` is cross-platform.
_WINDOWS_ONLY_PREFIX = "desktop_"
_WINDOWS_ONLY_EXCEPTIONS = frozenset({"desktop_filesystem"})


def _windows_only(name: str) -> bool:
    return name.startswith(_WINDOWS_ONLY_PREFIX) and name not in _WINDOWS_ONLY_EXCEPTIONS


def _external(name: str) -> bool:
    return name.startswith(_EXTERNAL_PREFIXES) or name in _EXTERNAL_TOOLS


def _test_evidence(tests_root: Path, names: set[str]) -> dict[str, bool]:
    """Which tools a test actually EXECUTES (not merely mentions, registers or gates).

    Two shapes count:

    * a call to the registered handler FUNCTION — ``_tool_run_command(...)`` — which is how
      most MCP unit tests drive a tool;
    * a JSON-RPC ``tools/call`` payload in a file that also dispatches it
      (``_handle_tools_call``/``handle_message``), which is how the integration tests do it.

    Deliberately NOT evidence: the tool name in a comment, in a gate assertion
    (``check_sync("cdp_snapshot", …)``), in ``_runs_concurrently``, or in a registration
    assertion. Each of those was a false PASS in the first draft of this script — they
    prove the catalogue knows the tool, not that anything ever ran it.
    """
    from navig.mcp_server import MCPProtocolHandler

    handlers = getattr(MCPProtocolHandler(), "_tool_handlers", {})
    dispatch_markers = ("_handle_tools_call(", "handle_message(", ".call_tool(")

    handler_patterns: dict[str, re.Pattern[str]] = {}
    payload_patterns: dict[str, re.Pattern[str]] = {}
    for name in names:
        function_name = getattr(handlers.get(name), "__name__", "") if callable(handlers.get(name)) else ""
        # A generic name ("handler", "_handler") would match half the test tree.
        if len(function_name) >= 8 and function_name not in {"handler", "_handler"}:
            handler_patterns[name] = re.compile(rf'\b{re.escape(function_name)}\s*\(')
        payload_patterns[name] = re.compile(rf'["\']name["\']\s*:\s*["\']{re.escape(name)}["\']')

    evidence = dict.fromkeys(names, False)
    for path in tests_root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        dispatches = any(marker in text for marker in dispatch_markers)
        for name in names:
            if evidence[name]:
                continue
            pattern = handler_patterns.get(name)
            calls_handler = pattern is not None and pattern.search(text) is not None
            dispatches_call = dispatches and payload_patterns[name].search(text) is not None
            if calls_handler or dispatches_call:
                evidence[name] = True
    return evidence


def build(core_root: Path) -> dict[str, Any]:
    inventory_path = core_root.parent / "reports" / "quality" / "mcp-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    tools = {row["name"]: row for row in inventory["tools"]}
    evidence = _test_evidence(core_root / "tests", set(tools))

    rows: list[dict[str, Any]] = []
    for name, row in sorted(tools.items()):
        structural_issues = []
        if not row["handler_registered"]:
            structural_issues.append("no handler")
        if not row["strict_schema"]:
            structural_issues.append("non-strict schema")
        if not row["all_params_documented"]:
            structural_issues.append("undocumented parameter")
        if row["description_chars"] < 40:
            structural_issues.append("description too thin")

        if structural_issues:
            verdict, reason = "FAIL", "; ".join(structural_issues)
        elif _windows_only(name) and DEPLOYMENT_PLATFORM != "win32":
            verdict, reason = "UNSUPPORTED", "Windows UI automation; not advertised on the deployment platform"
        elif _external(name):
            verdict = "PASS" if evidence[name] else "BLOCKED"
            reason = (
                "unit evidence present; live check not attempted"
                if evidence[name]
                else "needs a live account/service this audit does not own"
            )
        elif evidence[name]:
            verdict, reason = "PASS", "test drives the tool"
        else:
            verdict, reason = "PARTIAL", "structure verified, behaviour known by inspection only"

        rows.append(
            {
                "tool": name,
                "family": row["family"],
                "verdict": verdict,
                "reason": reason,
                "test_evidence": evidence[name],
                "structural_issues": structural_issues,
                "read_only": row["read_only"],
                "destructive": row["destructive"],
                "live_check": "not attempted" if _external(name) or _windows_only(name) else "not required for this verdict",
            }
        )

    counts = Counter(row["verdict"] for row in rows)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": DEPLOYMENT_PLATFORM,
        "catalogue": {
            "tools": len(rows),
            "advertised_on_platform": len([row for row in rows if row["verdict"] != "UNSUPPORTED"]),
        },
        "counts": dict(sorted(counts.items())),
        "rules": {
            "PASS": "structural checks pass AND a test drives the tool",
            "PARTIAL": "structural checks pass, no test drives it",
            "BLOCKED": "needs a live third-party account/service this audit does not own",
            "UNSUPPORTED": f"cannot run on {DEPLOYMENT_PLATFORM}",
            "FAIL": "a structural check fails",
        },
        "tools": rows,
    }


def main(argv: list[str] | None = None) -> int:
    del argv
    core_root = Path(__file__).resolve().parents[1]
    matrix = build(core_root)
    out = core_root.parent / "reports" / "quality" / "verdict-matrix.json"
    out.write_text(json.dumps(matrix, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = matrix["counts"]
    print(
        f"wrote {out} — {matrix['catalogue']['tools']} tools, "
        f"{matrix['catalogue']['advertised_on_platform']} advertised on {matrix['platform']}: "
        + ", ".join(f"{verdict} {count}" for verdict, count in counts.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
