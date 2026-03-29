#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / ".local" / "help_audit"


@dataclass
class Node:
    path: list[str]
    kind: str
    hidden: bool


@dataclass
class AuditRow:
    command: str
    help_syntax: str
    exit_code: int
    output_valid: bool
    issue_found: str
    required: bool
    stdout: str
    stderr: str
    duration_ms: int
    validation: dict[str, Any]
    root_cause: str
    recommended_fix: str


def _command_name_from_info(cmd_info: Any) -> str | None:
    name = getattr(cmd_info, "name", None)
    if name:
        return str(name)

    callback = getattr(cmd_info, "callback", None)
    if callback is None:
        return None

    callback_name = getattr(callback, "__name__", None)
    if not callback_name:
        return None
    return callback_name.replace("_", "-")


def collect_nodes(typer_app: Any, prefix: list[str] | None = None) -> list[Node]:
    prefix = prefix or []
    nodes: list[Node] = []

    for cmd_info in getattr(typer_app, "registered_commands", []):
        name = _command_name_from_info(cmd_info)
        if not name:
            continue
        nodes.append(
            Node(
                path=[*prefix, name],
                kind="command",
                hidden=bool(getattr(cmd_info, "hidden", False)),
            )
        )

    for group_info in getattr(typer_app, "registered_groups", []):
        group_name = getattr(group_info, "name", None)
        group_typer = getattr(group_info, "typer_instance", None)
        if not group_name or group_typer is None:
            continue
        group_path = [*prefix, str(group_name)]
        nodes.append(
            Node(
                path=group_path,
                kind="group",
                hidden=bool(getattr(group_info, "hidden", False)),
            )
        )
        nodes.extend(collect_nodes(group_typer, group_path))

    return nodes


def build_inventory(nodes: list[Node]) -> dict[str, list[str]]:
    inventory: dict[str, set[str]] = {}
    for node in nodes:
        if not node.path:
            continue
        top = node.path[0]
        inventory.setdefault(top, set())
        if len(node.path) >= 2:
            inventory[top].add(node.path[1])
    return {key: sorted(values) for key, values in sorted(inventory.items())}


def analyze_failure(stdout: str, stderr: str, exit_code: int, required: bool) -> tuple[str, str]:
    combined = f"{stdout}\n{stderr}".lower()

    if exit_code == 0:
        if required:
            return (
                "Invocation returned success but did not render help output",
                "Ensure this command path supports `--help` and returns standard help sections.",
            )
        return (
            "Best-effort syntax executed command logic instead of help",
            "Use `--help` for this command path, or implement explicit `help` handling if compatibility is needed.",
        )

    if "traceback (most recent call last)" in combined:
        return (
            "Unhandled exception during help rendering",
            "Inspect traceback and fix failing import/handler initialization in command module.",
        )
    if "no such command" in combined and " help" in combined:
        return (
            "Literal help subcommand is not implemented for this command path",
            "Treat `<command> help` as informational only, or implement explicit `help` subcommands where desired.",
        )
    if "no such option: -h" in combined:
        return (
            "Short `-h` is not configured for this command path",
            "Prefer `--help`, or add `-h` aliases only where it does not conflict with existing flags.",
        )
    if "registration failed" in combined and "command" in combined:
        return (
            "Command registration/import failed",
            "Fix import-time error in command module and re-run help audit.",
        )
    if "got unexpected extra argument (help)" in combined:
        return (
            "Parser treats `help` as an unexpected positional argument",
            "Use `--help`; optionally add explicit `help` command compatibility shim.",
        )

    if required:
        return (
            "Required help invocation failed",
            "Investigate parser/registration behavior for this command path.",
        )

    return (
        "Best-effort help syntax unsupported",
        "No change required unless you want compatibility with this help syntax.",
    )


def validate_output(args: list[str], exit_code: int, stdout: str, stderr: str) -> tuple[bool, dict[str, bool], str]:
    output = (stdout or "") + ("\n" + stderr if stderr else "")
    output_lower = output.lower()

    non_empty = bool(output.strip())
    has_usage = bool(re.search(r"\busage\b", output_lower))
    has_structure = any(token in output_lower for token in ("options", "commands", "--help", "show this message"))

    non_flag_tokens = [token for token in args if not token.startswith("-")]
    expected_name = non_flag_tokens[-1].lower() if non_flag_tokens else "navig"
    has_command_name = expected_name in output_lower

    has_traceback = "traceback (most recent call last)" in output_lower
    has_raw_exception = bool(re.search(r"\bexception\b", output_lower)) and has_traceback

    help_markers_ok = has_usage or has_structure

    valid = all(
        [
            exit_code == 0,
            non_empty,
            help_markers_ok,
            not has_traceback,
            not has_raw_exception,
        ]
    )

    return valid, {
        "exit_zero": exit_code == 0,
        "non_empty": non_empty,
        "has_usage": has_usage,
        "has_structure": has_structure,
        "has_command_name": has_command_name,
        "help_markers_ok": help_markers_ok,
        "no_traceback": not has_traceback,
    }, expected_name


def run_invocation(cli_name: str, args: list[str], required: bool, timeout: int) -> AuditRow:
    command_display = f"{cli_name} {' '.join(args)}".strip()

    start = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "navig", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "PYTHONUTF8": "1", "NAVIG_SKIP_ONBOARDING": "1"},
        )
        exit_code = int(result.returncode)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
    except subprocess.TimeoutExpired as timeout_error:
        duration = int((time.perf_counter() - start) * 1000)
        stdout = timeout_error.stdout or ""
        stderr = (timeout_error.stderr or "") + "\n[help-audit] timeout"
        return AuditRow(
            command=command_display,
            help_syntax=command_display,
            exit_code=124,
            output_valid=False,
            issue_found="Timeout",
            required=required,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration,
            validation={"timeout": True},
            root_cause="Help invocation timed out",
            recommended_fix="Inspect command initialization path for blocking operations in help flow.",
        )

    duration = int((time.perf_counter() - start) * 1000)
    output_valid, validation, _expected_name = validate_output(args, exit_code, stdout, stderr)

    issue_found = "—"
    root_cause = ""
    recommended_fix = ""
    if not output_valid:
        root_cause, recommended_fix = analyze_failure(stdout, stderr, exit_code, required)
        issue_found = root_cause or "Validation failed"

    return AuditRow(
        command=" ".join(args) if args else "(root)",
        help_syntax=command_display,
        exit_code=exit_code,
        output_valid=output_valid,
        issue_found=issue_found,
        required=required,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration,
        validation=validation,
        root_cause=root_cause,
        recommended_fix=recommended_fix,
    )


def build_invocations(nodes: list[Node]) -> list[tuple[list[str], bool]]:
    invocations: list[tuple[list[str], bool]] = []

    # Global help forms
    invocations.append((["help"], True))
    invocations.append((["--help"], True))
    invocations.append((["-h"], False))

    unique_paths: list[list[str]] = sorted({tuple(node.path) for node in nodes}, key=lambda x: (len(x), x))

    for path in unique_paths:
        path_list = list(path)
        invocations.append((path_list + ["--help"], True))
        invocations.append((path_list + ["help"], False))
        invocations.append((path_list + ["-h"], False))

    dedup: list[tuple[list[str], bool]] = []
    seen: set[tuple[str, ...]] = set()
    for args, required in invocations:
        key = tuple(args)
        if key in seen:
            continue
        seen.add(key)
        dedup.append((args, required))
    return dedup


def write_reports(out_dir: Path, inventory: dict[str, list[str]], rows: list[AuditRow]) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    inventory_path = out_dir / "command_inventory.json"
    inventory_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")

    rows_json_path = out_dir / "help_audit_rows.json"
    rows_json_path.write_text(json.dumps([asdict(row) for row in rows], indent=2), encoding="utf-8")

    rows_csv_path = out_dir / "help_audit_table.csv"
    with rows_csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Command", "Help Syntax", "Exit Code", "Output Valid", "Issue Found", "Required"])
        for row in rows:
            writer.writerow(
                [
                    row.command,
                    row.help_syntax,
                    row.exit_code,
                    "✅" if row.output_valid else "❌",
                    row.issue_found,
                    "yes" if row.required else "no",
                ]
            )

    failures = [row for row in rows if not row.output_valid]
    failures_path = out_dir / "help_audit_failures.json"
    failures_path.write_text(json.dumps([asdict(row) for row in failures], indent=2), encoding="utf-8")

    markdown_path = out_dir / "help_audit_report.md"
    with markdown_path.open("w", encoding="utf-8") as handle:
        handle.write("# NAVIG CLI Help Audit Report\n\n")
        handle.write(f"- Total invocations: {len(rows)}\n")
        handle.write(f"- Passed: {len(rows) - len(failures)}\n")
        handle.write(f"- Failed: {len(failures)}\n\n")

        handle.write("## Command Inventory\n\n")
        handle.write("```json\n")
        handle.write(json.dumps(inventory, indent=2))
        handle.write("\n```\n\n")

        handle.write("## Results Table\n\n")
        handle.write("| Command | Help Syntax | Exit Code | Output Valid | Issue Found |\n")
        handle.write("|---|---|---:|:---:|---|\n")
        for row in rows:
            handle.write(
                f"| `{row.command}` | `{row.help_syntax}` | {row.exit_code} | {'✅' if row.output_valid else '❌'} | {row.issue_found} |\n"
            )

        if failures:
            handle.write("\n## Failure Details\n\n")
            for index, row in enumerate(failures, start=1):
                handle.write(f"### {index}. `{row.help_syntax}`\n\n")
                handle.write(f"- Root cause: {row.root_cause or 'Unknown'}\n")
                handle.write(f"- Recommended fix: {row.recommended_fix or 'Investigate manually'}\n")
                handle.write("- Stdout:\n\n```text\n")
                handle.write(row.stdout.strip() or "<empty>")
                handle.write("\n```\n")
                handle.write("- Stderr:\n\n```text\n")
                handle.write(row.stderr.strip() or "<empty>")
                handle.write("\n```\n\n")

    return {
        "inventory": inventory_path,
        "rows_json": rows_json_path,
        "rows_csv": rows_csv_path,
        "failures_json": failures_path,
        "report_md": markdown_path,
    }


def load_core_nodes(include_hidden: bool) -> list[Node]:
    import navig.cli as cli

    cli._register_external_commands(register_all=True)
    nodes = collect_nodes(cli.app)
    if not include_hidden:
        nodes = [node for node in nodes if not node.hidden]
    return nodes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit all CLI help surfaces for built-in NAVIG commands")
    parser.add_argument("--cli-name", default="navig", help="Display name for command invocations")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory for audit reports")
    parser.add_argument("--timeout", type=int, default=30, help="Per-invocation timeout in seconds")
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden commands/aliases in audit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)

    nodes = load_core_nodes(include_hidden=args.include_hidden)
    inventory = build_inventory(nodes)
    invocations = build_invocations(nodes)

    rows: list[AuditRow] = []
    for invocation_args, required in invocations:
        rows.append(run_invocation(args.cli_name, invocation_args, required, args.timeout))

    paths = write_reports(out_dir, inventory, rows)

    failures = [row for row in rows if not row.output_valid]
    print(f"Inventory commands: {len(inventory)}")
    print(f"Invocations tested: {len(rows)}")
    print(f"Failures: {len(failures)}")
    for key, value in paths.items():
        print(f"{key}: {value}")

    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
