"""
Phase 1 legacy purge: remove hidden deprecated command blocks from navig/cli/__init__.py.
Each DELETE_MARKERS entry defines a (start_marker, end_marker) pair — everything from
start_marker (inclusive) up to but NOT including end_marker is removed.
Run from repo root: python .dev/remove_legacy_cmds.py
"""

import re
from pathlib import Path

CLI_FILE = Path("navig/cli/__init__.py")

# Each tuple: (unique_start_fragment, unique_end_fragment)
# The section from start_fragment through end_fragment EXCLUSIVE is deleted.
DELETE_SECTIONS = [
    # ── Monitor app + legacy monitor-* top-level commands ────────────────────
    (
        "\n# ============================================================================\n"
        "# DATABASE COMMANDS (Unified 'db' group)\n"
        "# ============================================================================\n"
        "\n"
        "monitor_app = typer.Typer(",
        "\n# ============================================================================\n"
        "# SECURITY MANAGEMENT (Unified 'security' group)\n"
        "# ============================================================================\n",
    ),
    # ── Security app + legacy firewall-*, security-scan top-level commands ───
    (
        "security_app = typer.Typer(\n"
        "    help=\"[DEPRECATED: Use 'navig host security'] Security management\",",
        "\n# ============================================================================\n"
        "# SYSTEM MAINTENANCE (Unified 'system' group - Pillar 7)\n"
        "# ============================================================================\n",
    ),
    # ── Inline system_app (dead code) + legacy flat maintenance commands ──────
    (
        "system_app = typer.Typer(\n"
        "    help=\"[DEPRECATED: Use 'navig host maintenance'] System maintenance\",",
        "\n# ============================================================================\n"
        "# REMOTE COMMAND EXECUTION\n"
        "# ============================================================================\n",
    ),
    # ── File legacy commands section ─────────────────────────────────────────
    (
        "\n# ============================================================================\n"
        "# FILE OPERATIONS (Legacy flat commands - deprecated, use 'navig file' group)\n"
        "# ============================================================================\n",
        "\n# ============================================================================\n"
        "# DOCKER MANAGEMENT COMMANDS\n"
        "# ============================================================================\n",
    ),
    # ── Advanced DB + Docker DB + server monitoring legacy commands ───────────
    (
        "\n# ============================================================================\n"
        "# ADVANCED DATABASE COMMANDS (DEPRECATED - use 'navig db <subcommand>')\n"
        "# ============================================================================\n",
        "\n# ============================================================================\n"
        "# AI ASSISTANT (Unified 'ai' group - Pillar 6: Intelligence)\n"
        "# ============================================================================\n",
    ),
]


def remove_sections(content: str, sections: list[tuple[str, str]]) -> tuple[str, list[str]]:
    removed = []
    for start_frag, end_frag in sections:
        start_pos = content.find(start_frag)
        if start_pos == -1:
            print(f"  [SKIP] Start fragment not found: {start_frag[:60]!r}…")
            continue
        end_pos = content.find(end_frag, start_pos + len(start_frag))
        if end_pos == -1:
            print(f"  [SKIP] End fragment not found: {end_frag[:60]!r}…")
            continue
        removed_text = content[start_pos:end_pos]
        lines_removed = removed_text.count("\n")
        content = content[:start_pos] + content[end_pos:]
        removed.append(
            f"Removed {lines_removed} lines starting with: {start_frag[:60]!r}…"
        )
    return content, removed


def main() -> None:
    original = CLI_FILE.read_text(encoding="utf-8")
    original_lines = original.count("\n")

    modified, log = remove_sections(original, DELETE_SECTIONS)
    final_lines = modified.count("\n")

    print(f"Original: {original_lines} lines")
    for entry in log:
        print(f"  ✓ {entry}")
    print(f"Final:    {final_lines} lines  (removed {original_lines - final_lines} lines)")

    CLI_FILE.write_text(modified, encoding="utf-8")
    print("Done. File written.")


if __name__ == "__main__":
    main()
