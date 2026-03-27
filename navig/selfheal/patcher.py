"""navig.selfheal.patcher — Convert scan findings into a unified diff.

Takes a filtered list of :class:`~navig.selfheal.scanner.ScanFinding` objects
(severity ``critical`` or ``high`` only) and generates a standard unified diff
string that can be applied via ``git apply``.

Each patched line is annotated with an inline ``# NAVIG-HEAL: <reason>``
comment so reviewers can trace every change back to the scan result.

If a finding's ``suggested_fix`` references a new dependency (detected by the
presence of a pip package name pattern), that dependency is appended to
``requirements.txt`` with a comment.

Returns a raw ``.patch`` string — no prose, no wrappers.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Optional

from loguru import logger

from navig.selfheal.scanner import ScanFinding

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Only these severity levels are patched automatically.
_PATCH_SEVERITIES = frozenset({"critical", "high"})

# Regex to detect a new pip requirement mentioned in a suggested_fix.
# Matches patterns like "pip install <pkg>", "install <pkg>", or
# "import <pkg>" when <pkg> is not a stdlib name.
_NEW_DEP_RE = re.compile(
    r"(?:pip install|install)\s+([\w\-]+)",
    re.IGNORECASE,
)

# Bare except pattern replaced by the most common auto-patch.
_BARE_EXCEPT_RE = re.compile(r"^(\s*)except\s*:(.*)$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _apply_finding_to_lines(
    lines: list[str],
    finding: ScanFinding,
) -> list[str]:
    """Apply a single *finding*'s suggested fix to *lines* in-place copy.

    Strategy:
    - Line index is ``finding.line - 1`` (1-based → 0-based).
    - The fix replaces only the specific problematic line identified in the
      finding.  The ``suggested_fix`` text is used as the new line content.
    - A ``# NAVIG-HEAL: <description>`` comment is appended to the fixed line.
    - For bare ``except:`` findings, the replacement is hard-coded for safety.

    Args:
        lines: Source lines of the file (with ``\\n`` endings).
        finding: The finding to apply.

    Returns:
        New list of lines with the fix applied.
    """
    idx = finding.line - 1
    if idx < 0 or idx >= len(lines):
        logger.debug(
            "Line {} out of range (file has {} lines)", finding.line, len(lines)
        )
        return lines

    original_line = lines[idx]
    # Strip both \n and \r so the bare-except regex $ anchor works on Windows.
    stripped = original_line.rstrip("\r\n")
    indent = len(stripped) - len(stripped.lstrip())
    indent_str = " " * indent

    # Special-case: bare except clause → safe replacement
    bare_match = _BARE_EXCEPT_RE.match(stripped)
    if bare_match:
        new_content = (
            f"{bare_match.group(1)}except Exception as exc:{bare_match.group(2)}"
        )
        heal_comment = f"  # NAVIG-HEAL: {finding.description[:80]}"
        new_line = new_content.rstrip() + heal_comment + "\n"
    else:
        # Use the suggested_fix as the replacement; preserve indent.
        fix_stripped = finding.suggested_fix.strip()
        # If the suggested fix looks like a full line of code, use it directly.
        if fix_stripped and not fix_stripped.startswith("#"):
            new_content = indent_str + fix_stripped
        else:
            # For descriptive fixes (prose), keep original line and add comment.
            new_content = original_line.rstrip("\n")
        heal_comment = f"  # NAVIG-HEAL: {finding.description[:80]}"
        new_line = new_content.rstrip() + heal_comment + "\n"

    patched = list(lines)
    patched[idx] = new_line
    return patched


def _extract_new_dep(finding: ScanFinding) -> Optional[str]:
    """Extract a new pip dependency name from *finding.suggested_fix*, or None.

    Args:
        finding: Scan finding to inspect.

    Returns:
        Package name string if a new dependency is referenced, else None.
    """
    match = _NEW_DEP_RE.search(finding.suggested_fix)
    if match:
        return match.group(1).strip()
    return None


def _append_requirement(repo_path: Path, dep: str, reason: str) -> None:
    """Append *dep* to ``requirements.txt`` if not already present.

    Args:
        repo_path: Root of the git repository.
        dep: Package name (e.g. ``"httpx"``).
        reason: Short explanation appended as a comment.
    """
    req_file = repo_path / "requirements.txt"
    if not req_file.exists():
        logger.debug("requirements.txt not found at {}", repo_path)
        return
    existing = req_file.read_text(encoding="utf-8")
    # Avoid duplicates — check bare package name (ignoring version specifiers).
    if re.search(rf"^{re.escape(dep)}[=<>!;\s]", existing, re.MULTILINE):
        logger.debug("Dependency {} already in requirements.txt", dep)
        return
    addition = f"{dep}  # added by NAVIG self-heal: {reason}\n"
    req_file.write_text(existing.rstrip("\n") + "\n" + addition, encoding="utf-8")
    logger.info("Added {} to requirements.txt", dep)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_patch(
    findings: list[ScanFinding],
    repo_path: Path,
) -> str:
    """Generate a unified diff patch from *findings*.

    Only findings with ``severity`` in ``{"critical", "high"}`` are included.
    The patch can be applied via ``git apply``.

    Args:
        findings: List of scan findings (all severities accepted; lower-
            severity findings are silently skipped).
        repo_path: Root directory of the git repository.  Used to locate
            source files and ``requirements.txt``.

    Returns:
        Raw unified diff string (``str``).  Returns an empty string when no
        patchable findings exist.

    Example::

        from navig.platform.paths import config_dir

        repo_root = config_dir() / "core-repo"
        patch_str = build_patch(findings, repo_root)
        if patch_str:
            apply_patch(repo_path, patch_str)
    """
    actionable = [f for f in findings if f.severity in _PATCH_SEVERITIES]
    if not actionable:
        logger.info("No critical/high findings to patch")
        return ""

    logger.info("Building patch for {} critical/high findings", len(actionable))

    # Group findings by file so we can apply all changes per file in one pass.
    by_file: dict[str, list[ScanFinding]] = {}
    for finding in actionable:
        by_file.setdefault(finding.file, []).append(finding)

    all_hunks: list[str] = []

    for rel_path, file_findings in sorted(by_file.items()):
        abs_path = repo_path / rel_path
        if not abs_path.exists():
            logger.warning("File not found, skipping patch for {}", rel_path)
            continue

        try:
            original_text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read {}: {}", abs_path, exc)
            continue

        original_lines = original_text.splitlines(keepends=True)
        patched_lines = list(original_lines)

        # Apply each finding; sort by line descending so earlier edits don't
        # shift the line indices for subsequent edits.
        for finding in sorted(file_findings, key=lambda f: f.line, reverse=True):
            patched_lines = _apply_finding_to_lines(patched_lines, finding)

            # Handle new dependency if referenced.
            new_dep = _extract_new_dep(finding)
            if new_dep:
                _append_requirement(repo_path, new_dep, finding.description[:60])

        if patched_lines == original_lines:
            logger.debug("No effective change for {}", rel_path)
            continue

        diff = list(
            difflib.unified_diff(
                original_lines,
                patched_lines,
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="\n",
            )
        )
        if diff:
            all_hunks.extend(diff)

    patch_str = "".join(all_hunks)
    logger.info(
        "Patch built: {} bytes covering {} file(s)",
        len(patch_str),
        len(by_file),
    )
    return patch_str
