#!/usr/bin/env python3
"""
Export NAVIG command registry artifacts.

Regenerate the committed manifest under **Python 3.13** (the canonical navig
interpreter) with the first-party plugins installed — any other interpreter
silently SHRINKS the catalog and the guard below will refuse the write.

Usage:
    python tools/export_registry.py --validate --format both --deprecations-report
    python tools/export_registry.py --include-hidden --output-dir generated
    # override the interpreter guard (must equal the running major.minor):
    python tools/export_registry.py --allow-interpreter 3.14 --format both
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The manifest must reflect THIS source tree, not whatever `navig` a stale editable
# install resolves to. Run as a script, sys.path[0] is tools/ (not the core root), so a
# PEP 660 editable finder pointing at a *different* checkout — or a static finder whose
# module map predates newer command modules — silently drops core commands (navig
# ledger/audit/undo/skill distill/space books were all missing: a 1285->1276 shrink that
# passed the cross-artifact guards). Prepend the core root so `import navig` binds to the
# co-located source. MUST run before importing navig.
_CORE_ROOT = Path(__file__).resolve().parent.parent
if sys.path[:1] != [str(_CORE_ROOT)]:
    sys.path.insert(0, str(_CORE_ROOT))

from navig.registry.manifest import (  # noqa: E402 — must follow the sys.path pin above
    build_full_manifest,
    build_public_manifest,
    deprecations_report,
    render_markdown,
    validate_manifest,
)

# The interpreter that produced the committed manifest. A regen under any other version,
# or with the first-party plugins absent, shrinks the catalog — see _interpreter_guard.
CANONICAL_INTERPRETER = "3.13"


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


def _plugin_command_count(manifest: dict) -> int:
    """Number of commands contributed by first-party plugin packages.

    A plugin command's ``module`` sits under its own top-level package (``navig_download``,
    ``navig_github``, …); core commands are under ``navig``. Zero here means no plugins
    loaded — the strongest positive signal that this interpreter would ship a bare-core
    (shrunken) catalog, independent of the plugin *set* (which varies by install).
    """
    n = 0
    for cmd in manifest.get("commands", []):
        if (cmd.get("module") or "navig").split(".", 1)[0] != "navig":
            n += 1
    return n


def _writing_to_tracked(output_dir: Path) -> bool:
    """True when the export targets the committed ``core/generated`` artifacts.

    Scoped deliberately: a custom ``--output-dir`` (a temp/dry-run compare, a test) is a
    deliberate sandbox write that can never become the committed catalog, so the guard
    never engages there — only a write to the real tracked location is gated.
    """
    try:
        return output_dir.resolve() == (_CORE_ROOT / "generated").resolve()
    except OSError:
        return False


def _interpreter_guard(manifest: dict, allow_interpreter: str | None) -> int:
    """Refuse to overwrite the tracked manifest from an interpreter that would SHRINK it.

    Two silent, previously-committable shrink modes:

    * **wrong Python** — the first-party plugins are installed in 3.13's site-packages, so
      a 3.14 regen drops ~160 plugin commands (measured 1285->1122).
    * **no plugins** — a bare-core interpreter exports 0 plugin commands.

    Soft by design: a matching ``--allow-interpreter <running-version>`` overrides, and a
    non-tracked ``--output-dir`` never reaches this check (so ``--validate`` against a temp
    dir, or any dry-run compare, is never blocked). Returns 0 to proceed, 2 to refuse.
    """
    run_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    plugin_cmds = _plugin_command_count(manifest)

    concerns: list[str] = []
    if run_ver != CANONICAL_INTERPRETER:
        concerns.append(
            f"running Python {run_ver}; the committed manifest is built under "
            f"{CANONICAL_INTERPRETER} (its first-party plugins live in {CANONICAL_INTERPRETER}'s "
            "site-packages)"
        )
    if plugin_cmds == 0:
        concerns.append(
            f"exported {manifest.get('total', 0)} commands but 0 come from first-party plugin "
            "packages (navig_*) - the plugins are not installed in this interpreter"
        )

    if not concerns:
        return 0

    print(
        "[INTERPRETER GUARD] this export would overwrite the tracked command manifest:",
        file=sys.stderr,
    )
    for concern in concerns:
        print(f"  ! {concern}", file=sys.stderr)
    print(
        "  Writing now would SHRINK the committed catalog and could be committed unnoticed "
        "(the cross-artifact freshness guards stay green on a consistently-shrunken manifest).",
        file=sys.stderr,
    )
    print(
        f"  Fix: regenerate under Python {CANONICAL_INTERPRETER} with the first-party plugins "
        "installed.",
        file=sys.stderr,
    )
    print(
        f"  Override (you accept this interpreter): --allow-interpreter {run_ver}",
        file=sys.stderr,
    )

    if allow_interpreter == run_ver:
        print(
            f"[INTERPRETER GUARD] override accepted (--allow-interpreter {run_ver}); writing anyway.",
            file=sys.stderr,
        )
        return 0
    if allow_interpreter:
        print(
            f"[INTERPRETER GUARD] --allow-interpreter {allow_interpreter} does not match the "
            f"running interpreter {run_ver}; refusing to write.",
            file=sys.stderr,
        )
    return 2


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
    parser.add_argument(
        "--allow-interpreter",
        metavar="X.Y",
        default=None,
        help=(
            "Acknowledge a non-canonical interpreter (must equal the running major.minor, "
            f"e.g. --allow-interpreter 3.14) to write the tracked manifest anyway. Canonical is "
            f"Python {CANONICAL_INTERPRETER}; a mismatch (or missing plugins) silently shrinks the "
            "catalog. Ignored when --output-dir is not the tracked generated/ directory."
        ),
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

    # Guard the tracked catalog only: a write to core/generated from an interpreter that
    # would shrink the manifest is refused unless explicitly acknowledged. Runs AFTER
    # validation so --validate still reports on its own merits; a temp/custom output-dir
    # (dry-run compare, tests) is never gated.
    if _writing_to_tracked(output_dir):
        guard_rc = _interpreter_guard(manifest, args.allow_interpreter)
        if guard_rc != 0:
            return guard_rc

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
