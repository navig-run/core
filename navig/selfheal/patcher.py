"""navig.selfheal.patcher — Convert scan findings into a unified diff.

Takes a filtered list of :class:`~navig.selfheal.scanner.ScanFinding` objects
(severity ``critical`` or ``high`` only) and generates a standard unified diff
string that can be applied via ``git apply``.

Each patched line is annotated with an inline ``# NAVIG-HEAL: <reason>``
comment so reviewers can trace every change back to the scan result.

If a finding's ``suggested_fix`` references a new dependency (detected by the
presence of a pip package name pattern), a ``requirements.txt`` hunk is added
to the diff — so the reviewer sees it and ``git apply`` performs it.

Returns a raw ``.patch`` string — no prose, no wrappers, and no writes: building
a patch never touches the working tree.
"""

from __future__ import annotations

import difflib
import re
import textwrap
from pathlib import Path

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


def _one_line(text: str, limit: int = 80) -> str:
    """Collapse *text* to a single line so it is safe inside a trailing ``#`` comment.

    ``description`` and ``suggested_fix`` are LLM free-text. A newline in either used to end up
    INSIDE one element of the line list, so ``difflib`` counted one line where the file had two:
    the extra physical line was emitted with no ``+``/``-``/space prefix and ``git apply`` refused
    the whole patch as corrupt.
    """
    return " ".join(text.split())[:limit]


def _fix_rows(suggested_fix: str, indent_str: str) -> list[str]:
    """Split a possibly multi-line ``suggested_fix`` into indented replacement rows."""
    body = textwrap.dedent(suggested_fix.strip("\n").replace("\r\n", "\n")).rstrip()
    rows: list[str] = []
    for row in body.splitlines():
        # Keep blank rows genuinely blank — trailing indent on an empty line is noise.
        rows.append(f"{indent_str}{row}".rstrip() if row.strip() else "")
    return rows


def _apply_finding_to_lines(
    lines: list[str],
    finding: ScanFinding,
) -> list[str]:
    """Apply a single *finding*'s suggested fix to *lines* in-place copy.

    Strategy:
    - Line index is ``finding.line - 1`` (1-based → 0-based).
    - The fix replaces the problematic line. A multi-line ``suggested_fix`` becomes SEVERAL list
      elements — never one element holding embedded newlines, which produced a corrupt diff.
      Splicing extra rows is safe because ``build_patch`` applies findings in descending line
      order, so only already-processed indices shift.
    - A single-line ``# NAVIG-HEAL: <description>`` comment is appended to the first fixed line.
    - For bare ``except:`` findings, the replacement is hard-coded for safety.

    Args:
        lines: Source lines of the file (with ``\\n`` endings).
        finding: The finding to apply.

    Returns:
        New list of lines with the fix applied.
    """
    idx = finding.line - 1
    if idx < 0 or idx >= len(lines):
        logger.debug("Line {} out of range (file has {} lines)", finding.line, len(lines))
        return lines

    original_line = lines[idx]
    # Strip both \n and \r so the bare-except regex $ anchor works on Windows.
    stripped = original_line.rstrip("\r\n")
    indent = len(stripped) - len(stripped.lstrip())
    indent_str = " " * indent
    heal_comment = f"  # NAVIG-HEAL: {_one_line(finding.description)}"

    # Special-case: bare except clause → safe replacement
    bare_match = _BARE_EXCEPT_RE.match(stripped)
    if bare_match:
        rows = [f"{bare_match.group(1)}except Exception as exc:{bare_match.group(2)}".rstrip()]
    else:
        fix_stripped = finding.suggested_fix.strip()
        # A fix that is only an install instruction ("pip install tenacity") is NOT a line of
        # code — we already parse it as a dependency and emit a requirements.txt hunk for it.
        # Substituting it verbatim wrote shell text into a Python file.
        is_dep_instruction = bool(_NEW_DEP_RE.match(fix_stripped)) and "\n" not in fix_stripped
        # If the suggested fix looks like code, use it directly; prose keeps the original line.
        if fix_stripped and not fix_stripped.startswith("#") and not is_dep_instruction:
            rows = _fix_rows(finding.suggested_fix, indent_str)
        else:
            rows = [stripped]
    if not rows:  # a suggested_fix of pure whitespace must not delete the line
        rows = [stripped]

    new_lines = [f"{row.rstrip()}{heal_comment if i == 0 else ''}\n" for i, row in enumerate(rows)]
    patched = list(lines)
    patched[idx : idx + 1] = new_lines
    return patched


def _extract_new_dep(finding: ScanFinding) -> str | None:
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


def _requirements_diff(repo_path: Path, deps: dict[str, str]) -> list[str]:
    """Return diff lines adding *deps* to ``requirements.txt`` — writing NOTHING to disk.

    This used to append to ``requirements.txt`` directly, from inside patch *generation*. Three
    problems, all fixed by emitting a hunk instead:

    * ``build_patch`` is documented (and used) as a pure "produce a diff" step, but it mutated the
      working tree as a side effect;
    * the caller builds the patch BEFORE its final "Submit this patch?" confirmation, so declining
      still left ``requirements.txt`` modified;
    * the change never appeared in the returned diff, so the human approved a patch that did not
      contain it — and ``commit_and_push`` runs ``git add --all``, sweeping an LLM-suggested
      dependency into the PR that nobody reviewed.

    Args:
        repo_path: Root of the git repository.
        deps: Mapping of package name → short reason.

    Returns:
        Unified-diff lines for ``requirements.txt``, or an empty list when there is nothing to add.
    """
    req_file = repo_path / "requirements.txt"
    if not req_file.exists():
        logger.debug("requirements.txt not found at {}", repo_path)
        return []
    try:
        existing = req_file.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Cannot read {}: {}", req_file, exc)
        return []

    additions: list[str] = []
    for dep, reason in deps.items():
        # Avoid duplicates — check bare package name (ignoring version specifiers).
        if re.search(rf"^{re.escape(dep)}[=<>!;\s]", existing, re.MULTILINE):
            logger.debug("Dependency {} already in requirements.txt", dep)
            continue
        additions.append(f"{dep}  # added by NAVIG self-heal: {_one_line(reason, 60)}\n")
    if not additions:
        return []

    original_lines = existing.splitlines(keepends=True)
    patched_lines = list(original_lines)
    if patched_lines and not patched_lines[-1].endswith("\n"):
        patched_lines[-1] += "\n"  # keep the file line-oriented before appending
    patched_lines.extend(additions)
    logger.info("Patch adds {} dependency line(s) to requirements.txt", len(additions))
    return list(
        difflib.unified_diff(
            original_lines,
            patched_lines,
            fromfile="a/requirements.txt",
            tofile="b/requirements.txt",
            lineterm="\n",
        )
    )


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
    # Collected, not written: the dependency additions ride in the returned diff so the reviewer
    # sees them and `git apply` performs them.
    new_deps: dict[str, str] = {}

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

            # Handle new dependency if referenced (emitted as a hunk after the file loop).
            new_dep = _extract_new_dep(finding)
            if new_dep:
                new_deps.setdefault(new_dep, finding.description)

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

    if new_deps:
        all_hunks.extend(_requirements_diff(repo_path, new_deps))

    patch_str = "".join(all_hunks)
    logger.info(
        "Patch built: {} bytes covering {} file(s)",
        len(patch_str),
        len(by_file),
    )
    return patch_str
