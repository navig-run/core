"""tool.py — CLI fallback for telemetry_auditor (spawn-per-call)."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import err  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from worker import cmd_scan, cmd_whois, cmd_sources, cmd_report, cmd_cmdref  # noqa: E402

TOOL = "telemetry_auditor"


def _print(result: dict, indent: bool = True) -> None:
    print(json.dumps(result, indent=2 if indent else None, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="navig telemetry audit",
        description="Privacy audit: VS Code + Windows telemetry connections, ownership tracing, risk classification.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  navig telemetry audit scan
  navig telemetry audit scan --filter vscode --save audit.json
  navig telemetry audit scan --filter windows --no-resolve
  navig telemetry audit whois --ip 13.107.42.16
  navig telemetry audit sources --save sources.json
  navig telemetry audit report --save full_report.json
  navig telemetry audit cmdref
""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── scan ──────────────────────────────────────────────────────────────────
    p_scan = sub.add_parser("scan", help="Step 1+2: capture all TCP connections, identify telemetry endpoints")
    p_scan.add_argument("--filter",    choices=["all", "vscode", "windows"], default="all",
                        help="Scope: 'vscode' for VS Code only, 'windows' for system telemetry, 'all' for everything")
    p_scan.add_argument("--resolve",   action="store_true",  default=True,  help="Resolve remote IPs to hostnames (default: on)")
    p_scan.add_argument("--no-resolve",action="store_true",  default=False, help="Skip PTR resolution (faster but no hostname matching)")
    p_scan.add_argument("--timeout",   type=int, default=60, metavar="SEC")
    p_scan.add_argument("--save",      metavar="FILE",       default=None,  help="Write JSON output to this file")

    # ── whois ─────────────────────────────────────────────────────────────────
    p_whois = sub.add_parser("whois", help="Step 3: trace ownership of a remote IP (RDAP, ASN, PTR, GeoIP)")
    p_whois.add_argument("--ip",      required=True, metavar="IP",  help="Remote IP address to investigate")
    p_whois.add_argument("--timeout", type=int, default=15, metavar="SEC")
    p_whois.add_argument("--save",    metavar="FILE", default=None)

    # ── sources ───────────────────────────────────────────────────────────────
    p_src = sub.add_parser("sources", help="Step 5: enumerate local telemetry staging files, ETL logs, registry keys")
    p_src.add_argument("--save", metavar="FILE", default=None)

    # ── report ────────────────────────────────────────────────────────────────
    p_rep = sub.add_parser("report", help="Step 6: full audit report with classified risk table (runs scan + sources)")
    p_rep.add_argument("--filter",    choices=["all", "vscode", "windows"], default="all")
    p_rep.add_argument("--no-resolve",action="store_true", default=False)
    p_rep.add_argument("--timeout",   type=int, default=90, metavar="SEC")
    p_rep.add_argument("--save",      metavar="FILE", default=None,
                        help="Write both JSON (.json) and Markdown (.md) report to this base path")

    # ── cmdref ────────────────────────────────────────────────────────────────
    sub.add_parser("cmdref", help="Print copy-pasteable command reference for manual audit steps 1–5")

    args = parser.parse_args()
    params = vars(args)
    command = params.pop("command")

    if command == "scan":
        no_resolve = params.pop("no_resolve", False)
        params["resolve"] = not no_resolve
        result = cmd_scan(params)

    elif command == "whois":
        result = cmd_whois(params)

    elif command == "sources":
        result = cmd_sources(params)

    elif command == "report":
        no_resolve = params.pop("no_resolve", False)
        params["resolve"] = not no_resolve
        result = cmd_report(params)

    elif command == "cmdref":
        result = cmd_cmdref(params)
        # Pretty-print cmdref as readable text, not raw JSON
        if result.get("ok"):
            data = result["data"]
            for key, section in data.items():
                if isinstance(section, dict):
                    print(f"\n{'═'*70}")
                    print(f"  {section.get('title', key)}")
                    print(f"{'═'*70}")
                    for k, v in section.items():
                        if k == "title":
                            continue
                        if isinstance(v, str):
                            print(f"  {k}:\n    {v}")
                        elif isinstance(v, dict):
                            print(f"  {k}:")
                            for kk, vv in v.items():
                                print(f"    {kk}: {vv}")
                        elif isinstance(v, list):
                            print(f"  {k}:")
                            for item in v:
                                print(f"    - {item}")
            sys.exit(0)
        else:
            _print(result)
            sys.exit(1)
        return

    else:
        result = err(TOOL, command, [f"Unknown command: {command}"])

    _print(result)

    # Print markdown table to stdout on report command for easy copy-paste
    if command == "report" and result.get("ok"):
        md = result["data"].get("markdown_table", "")
        if md:
            print("\n" + "─" * 70)
            print("  RISK TABLE (Markdown)")
            print("─" * 70)
            print(md)

    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
