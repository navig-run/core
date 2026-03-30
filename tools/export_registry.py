#!/usr/bin/env python3
"""
Export NAVIG command registry artifacts.

Usage:
    python tools/export_registry.py
    python tools/export_registry.py --validate --format both
    python tools/export_registry.py --include-hidden --output-dir generated
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from navig.registry.manifest import (
    build_full_manifest,
    build_public_manifest,
    deprecations_report,
    render_markdown,
    validate_manifest,
)


def _emit_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _emit_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _emit_completions(path: Path, manifest: dict) -> None:
    commands = [
        str(c.get("path", "")).strip()
        for c in manifest.get("commands", [])
        if isinstance(c, dict)
    ]
    commands = sorted(c for c in commands if c)
    _emit_text(path, "\n".join(commands) + ("\n" if commands else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Exit non-zero if command metadata validation fails.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "both"],
        default="json",
        help="Artifacts to generate.",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden/internal commands in exported registry.",
    )
    parser.add_argument(
        "--output-dir",
        default="generated",
        help="Output directory for generated artifacts.",
    )
    parser.add_argument(
        "--deprecations-report",
        action="store_true",
        help="Emit generated/deprecations.json report.",
    )

    args = parser.parse_args(argv)

    manifest = (
        build_full_manifest(validate=False)
        if args.include_hidden
        else build_public_manifest(validate=False)
    )

    if args.validate:
        try:
            validate_manifest(manifest)
        except ValueError as exc:
            print("[REGISTRY ERROR] validation failed", file=sys.stderr)
            for line in str(exc).splitlines():
                print(f"[REGISTRY ERROR] {line}", file=sys.stderr)
            return 1

    output_dir = Path(args.output_dir)

    if args.format in {"json", "both"}:
        json_path = output_dir / "commands.json"
        _emit_json(json_path, manifest)
        print(f"[OK] Exported {manifest['total']} commands -> {json_path}")

    if args.format in {"markdown", "both"}:
        markdown_path = output_dir / "commands.md"
        _emit_text(markdown_path, render_markdown(manifest))
        print(f"[OK] Markdown reference -> {markdown_path}")

    completions_path = output_dir / "completions" / "commands.txt"
    _emit_completions(completions_path, manifest)
    print(f"[OK] Completion source -> {completions_path}")

    if args.deprecations_report:
        report = deprecations_report(manifest)
        report_path = output_dir / "deprecations.json"
        _emit_json(report_path, report)
        print(f"[OK] Deprecations report -> {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
