"""Audit the instruction set a Claude Code session loads from a repository.

Claude Code loads the global ``~/.claude/CLAUDE.md``, the project ``CLAUDE.md``,
the project ``.claude/CLAUDE.md`` when present, and every ``@path`` imported by
the project root file. ``AGENTS.md`` is deliberately NOT counted: Claude Code
2.1.284 does not auto-load it (verified with a fresh-session probe), so counting
it would overstate every session.

The audit answers three questions that a plain byte count misses:

* how large is the *set* a session actually reads,
* where the same paragraph is duplicated across files (paying twice),
* whether relative Markdown links in those files still resolve.

Budgets apply to the repository files only. The global file is reported
separately because it is paid by every repository, not by this one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

DEFAULT_BUDGET_BYTES = 40_000
DEFAULT_FILE_BUDGET_BYTES = 24_000
BYTES_PER_TOKEN = 3.9
MIN_PARAGRAPH_CHARS = 300

_IMPORT_RE = re.compile(r"^@(.+?)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


@dataclass
class LoadedFile:
    """One file in the loaded set."""

    name: str
    path: Path
    data: bytes
    loaded: bool = True

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def lines(self) -> int:
        return self.data.count(b"\n")

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace")


@dataclass
class AuditResult:
    """Everything the audit found, in JSON-friendly form."""

    repo: str
    files: list[LoadedFile]
    global_file: Optional[LoadedFile]
    duplicate_bytes: int = 0
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    missing_links: list[dict[str, str]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def loaded_files(self) -> list[LoadedFile]:
        return [f for f in self.files if f.loaded]

    @property
    def repo_bytes(self) -> int:
        return sum(f.size for f in self.loaded_files)

    @property
    def tokens(self) -> int:
        return round(self.repo_bytes / BYTES_PER_TOKEN)

    @property
    def global_tokens(self) -> int:
        return round(self.global_file.size / BYTES_PER_TOKEN) if self.global_file else 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "files": [
                {
                    "file": f.name,
                    "bytes": f.size,
                    "lines": f.lines,
                    "tokens": round(f.size / BYTES_PER_TOKEN),
                    "loaded": f.loaded,
                }
                for f in self.files
            ],
            "global": (
                {
                    "path": str(self.global_file.path),
                    "bytes": self.global_file.size,
                    "tokens": self.global_tokens,
                }
                if self.global_file
                else None
            ),
            "repo_bytes": self.repo_bytes,
            "repo_tokens": self.tokens,
            "duplicate_bytes": self.duplicate_bytes,
            "duplicates": self.duplicates,
            "missing_links": self.missing_links,
            "violations": self.violations,
            "ok": not self.violations,
        }


def _imports(text: str) -> list[str]:
    return [m.group(1).strip() for m in _IMPORT_RE.finditer(text)]


def find_repo(start: Path) -> Path:
    """The nearest folder at or above *start* that holds a CLAUDE.md."""
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / "CLAUDE.md").is_file():
            return candidate
    return start


def _strip_code(text: str) -> str:
    """Remove fenced and inline code so example links are not checked."""
    return _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", text))


def discover(repo: Path, global_path: Optional[Path] = None) -> AuditResult:
    """Resolve the loaded set for *repo* without judging it."""
    repo = repo.resolve()
    root = repo / "CLAUDE.md"
    files: list[LoadedFile] = []
    if root.is_file():
        data = root.read_bytes()
        files.append(LoadedFile("CLAUDE.md", root, data))
        for target in _imports(data.decode("utf-8", "replace")):
            imported = (repo / target).resolve()
            if imported.is_file() and imported not in {f.path for f in files}:
                files.append(LoadedFile(target, imported, imported.read_bytes()))
    hidden = repo / ".claude" / "CLAUDE.md"
    if hidden.is_file() and hidden not in {f.path for f in files}:
        files.append(LoadedFile(".claude/CLAUDE.md", hidden, hidden.read_bytes()))

    gpath = Path(global_path).expanduser() if global_path else Path.home() / ".claude" / "CLAUDE.md"
    global_file = None
    if gpath.is_file():
        global_file = LoadedFile(str(gpath), gpath, gpath.read_bytes())
    return AuditResult(str(repo), files, global_file)


def _paragraphs(text: str) -> Iterable[str]:
    for block in re.split(r"\n\s*\n", text):
        normalized = re.sub(r"\s+", " ", block).strip()
        if len(normalized) >= MIN_PARAGRAPH_CHARS:
            yield normalized


def _find_duplicates(result: AuditResult) -> None:
    owners: dict[str, list[str]] = {}
    for loaded in result.loaded_files:
        for paragraph in _paragraphs(loaded.text):
            owners.setdefault(paragraph, []).append(loaded.name)
    if result.global_file is not None:
        for paragraph in _paragraphs(result.global_file.text):
            owners.setdefault(paragraph, []).append("global")

    for paragraph, names in owners.items():
        unique = sorted(set(names))
        if len(unique) < 2:
            continue
        result.duplicate_bytes += len(paragraph)
        result.duplicates.append(
            {
                "bytes": len(paragraph),
                "files": unique,
                "preview": paragraph[:120],
            }
        )
    result.duplicates.sort(key=lambda item: item["bytes"], reverse=True)


def _find_missing_links(result: AuditResult) -> None:
    for loaded in result.loaded_files:
        text = _strip_code(loaded.text)
        for match in _LINK_RE.finditer(text):
            target = match.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "#", "{")):
                continue
            path = target.split("#", 1)[0]
            if not path:
                continue
            if (loaded.path.parent / path).exists():
                continue
            result.missing_links.append({"file": loaded.name, "target": target})


def audit(
    repo: Path,
    *,
    global_path: Optional[Path] = None,
    budget_bytes: int = DEFAULT_BUDGET_BYTES,
    file_budget_bytes: int = DEFAULT_FILE_BUDGET_BYTES,
    max_duplicate_bytes: int = 0,
) -> AuditResult:
    """Audit the loaded instruction set for *repo* and apply the budgets."""
    result = discover(repo, global_path)
    if not result.files:
        result.violations.append("no CLAUDE.md in the repository root")
        return result
    _find_duplicates(result)
    _find_missing_links(result)

    if result.repo_bytes > budget_bytes:
        result.violations.append(
            f"loaded set is {result.repo_bytes:,} bytes, over the {budget_bytes:,}-byte budget"
        )
    for loaded in result.loaded_files:
        if loaded.size > file_budget_bytes:
            result.violations.append(
                f"{loaded.name} is {loaded.size:,} bytes, over the "
                f"{file_budget_bytes:,}-byte per-file budget"
            )
    if result.duplicate_bytes > max_duplicate_bytes:
        result.violations.append(
            f"{result.duplicate_bytes:,} bytes are duplicated across the set "
            f"(allowed: {max_duplicate_bytes:,})"
        )
    for missing in result.missing_links:
        result.violations.append(
            f"{missing['file']}: link target does not exist: {missing['target']}"
        )
    return result


def render(result: AuditResult) -> str:
    """Human-readable table plus the violation list."""
    lines = [
        f"instructions audit - {result.repo}",
        f"{'file':48} {'bytes':>7} {'lines':>6} {'~tok':>6}",
    ]
    for loaded in result.files:
        lines.append(
            f"{loaded.name:48} {loaded.size:>7,} {loaded.lines:>6,} "
            f"{round(loaded.size / BYTES_PER_TOKEN):>6,}"
        )
    lines.append(
        f"{'repo total':48} {result.repo_bytes:>7,} "
        f"{sum(f.lines for f in result.loaded_files):>6,} {result.tokens:>6,}"
    )
    if result.global_file is not None:
        lines.append(
            f"{'global (paid by every repo)':48} {result.global_file.size:>7,} "
            f"{result.global_file.lines:>6,} {result.global_tokens:>6,}"
        )
    lines.append(f"duplicated: {result.duplicate_bytes:,} bytes in {len(result.duplicates)} block(s)")
    if result.violations:
        lines.append("")
        lines.extend(f"FAIL: {violation}" for violation in result.violations)
    else:
        lines.append("ok - within budget, no duplicate blocks, links resolve")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the standalone script (used by pre-push and CI)."""
    import argparse

    parser = argparse.ArgumentParser(description="Audit the loaded instruction set")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--global-file", default=None)
    parser.add_argument("--budget-bytes", type=int, default=DEFAULT_BUDGET_BYTES)
    parser.add_argument("--file-budget-bytes", type=int, default=DEFAULT_FILE_BUDGET_BYTES)
    parser.add_argument("--max-duplicate-bytes", type=int, default=0)
    parser.add_argument("--check", action="store_true", help="exit 1 on violations")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    result = audit(
        find_repo(Path(args.repo)),
        global_path=Path(args.global_file) if args.global_file else None,
        budget_bytes=args.budget_bytes,
        file_budget_bytes=args.file_budget_bytes,
        max_duplicate_bytes=args.max_duplicate_bytes,
    )
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    elif not args.quiet or result.violations:
        print(render(result))
    return 1 if args.check and result.violations else 0
