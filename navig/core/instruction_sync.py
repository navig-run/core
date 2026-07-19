"""Cross-platform port of scripts/sync-instructions.ps1 (the mirror-generation core).

MASTER (``.github/instructions/MASTER.instructions.md``) is the single source of truth for
every AI tool's per-repo instructions. This module regenerates the mirrors from it — the
same transform the PowerShell script does, so byte-for-byte identical output (the tests
assert the committed mirrors match) — but in pure Python, so it runs on macOS/Linux too
and is reachable as ``navig sync instructions``.

Scope: the mirror generation + drift check + the ``.vscode/settings.json`` copilot-key
injection that must NEVER clobber a user's other settings. The PowerShell script keeps its
niche extras (sub-repo propagation, ``-SyncRigs`` space fan-out, macOS symlinks); those are
not ported here.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# target name -> repo-relative mirror path.
TARGET_PATHS: dict[str, str] = {
    "cursor": ".cursorrules",
    "copilot": ".github/copilot-instructions.md",
    "cline": ".cline/memory.md",
    "vscode": ".vscode/settings.json",
    "claude": ".claude/CLAUDE.md",
    "codex": ".codex/AGENTS.md",
    "gemini": ".gemini/GEMINI.md",
}

# The plain-markdown mirrors that are TRACKED and purely generated — drift-checkable.
# (.vscode mixes user prefs; .cline carries a timestamp; the dotfile mirrors are gitignored.)
CHECKABLE = ("cursor", "copilot")

MASTER_REL = ".github/instructions/MASTER.instructions.md"

# Case-insensitive to match PowerShell's `-match` / `-replace` default.
_HEADING = re.compile(r"^##\s")
_FULL_LINE_COMMENT = re.compile(r"^\s*<!--.*-->\s*$")
_INLINE_SKIP = re.compile(r"\s*<!--\s*SKIP:[a-z ]+-->\s*", re.IGNORECASE)


def _strip_front_matter(lines: list[str]) -> list[str]:
    if lines and lines[0].strip() == "---":
        end = 1
        while end < len(lines) and lines[end].strip() != "---":
            end += 1
        return lines[end + 1 :]
    return lines


def _remove_skipped_sections(lines: list[str], target: str) -> list[str]:
    """Drop each ``## …`` section whose heading carries ``<!-- SKIP:<target> -->``."""
    tag = re.compile(rf"<!--.*SKIP:{re.escape(target)}.*-->", re.IGNORECASE)
    out: list[str] = []
    skip = False
    for line in lines:
        if _HEADING.match(line):
            if tag.search(line):
                skip = True
                continue
            skip = False
        if not skip:
            out.append(line)
    return out


def process(raw_lines: list[str], target: str) -> str:
    """The `Get-Processed` transform: front-matter → SKIP sections → comments → trim."""
    lines = _strip_front_matter(raw_lines)
    lines = _remove_skipped_sections(lines, target)
    lines = [ln for ln in lines if not _FULL_LINE_COMMENT.match(ln)]  # strip full-line comments
    lines = [_INLINE_SKIP.sub("", ln) for ln in lines]               # strip inline SKIP tags
    while lines and lines[-1].strip() == "":                          # trim trailing blanks
        lines.pop()
    return "\n".join(lines)


def read_master(root: Path) -> list[str]:
    text = (root / MASTER_REL).read_text(encoding="utf-8")
    return text.splitlines()  # line endings stripped, like Get-Content


def render(target: str, raw_lines: list[str]) -> str:
    """The final content for *target* (some targets wrap the processed body)."""
    body = process(raw_lines, "copilot" if target != "cursor" else "cursor")
    if target == "cline":
        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        return f"# NAVIG — Agent Memory\n\nLast synced: {stamp}\n\n---\n\n{body}"
    return body


def _inject_vscode(existing: str | None, instruction_text: str) -> tuple[str | None, str]:
    """Return (new_json_text | None, status).

    Preserves EVERY existing key — only sets ``github.copilot.chat.codeGeneration.instructions``.
    Returns (None, "unparseable") when the current file can't be parsed, so the caller backs
    it up and leaves it untouched: clobbering a user's settings (colors, keybindings) is how
    this went wrong before, and is never acceptable.
    """
    data: dict = {}
    if existing is not None and existing.strip():
        stripped = re.sub(r"(?m)^\s*//.*$", "", existing)      # JSONC line comments
        stripped = re.sub(r"/\*[\s\S]*?\*/", "", stripped)     # JSONC block comments
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                data = parsed
        except (json.JSONDecodeError, ValueError):
            return None, "unparseable"
    data["github.copilot.chat.codeGeneration.instructions"] = [{"text": instruction_text}]
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n", "written"


def check_drift(root: Path) -> list[tuple[str, str]]:
    """Verify the TRACKED, purely-generated mirrors match MASTER. Returns [(label, state)].

    state ∈ {"in sync", "drift", "missing"}. No writes.
    """
    raw = read_master(root)
    results: list[tuple[str, str]] = []
    for target in CHECKABLE:
        rel = TARGET_PATHS[target]
        path = root / rel
        expected = process(raw, target)
        if not path.exists():
            results.append((rel, "missing"))
            continue
        actual = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        results.append((rel, "in sync" if actual == expected else "drift"))
    return results


def sync_all(root: Path, *, dry_run: bool = False) -> list[tuple[str, str, int]]:
    """Regenerate every mirror from MASTER. Returns [(rel_path, status, char_count)].

    status ∈ {"written", "dry", "unparseable", "unchanged"}.
    """
    raw = read_master(root)
    out: list[tuple[str, str, int]] = []
    for target, rel in TARGET_PATHS.items():
        path = root / rel
        if target == "vscode":
            existing = path.read_text(encoding="utf-8") if path.exists() else None
            new_text, status = _inject_vscode(existing, process(raw, "copilot"))
            if status == "unparseable":
                if not dry_run and path.exists():
                    path.with_suffix(path.suffix + ".bak").write_text(existing or "", encoding="utf-8")
                out.append((rel, "unparseable", 0))
                continue
            content = new_text or ""
        else:
            content = render(target, raw)

        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        out.append((rel, "dry" if dry_run else "written", len(content)))
    return out
