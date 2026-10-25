"""
navig.agent.tools.repo_search_tools — native repository search tools.

Provides ``file_search`` (glob filename/path search) and ``grep_search``
(content search) for the agentic ReAct loop.  These are first-party NAVIG
tools built on NAVIG's own registry, workspace resolution and result caps —
they do not wrap another harness's search surface.

Design constraints (Mission B plan, Phase 2):
- **Read-only** by construction: no writes, no process mutation.
- **Workspace-bounded**: relative paths must stay under the active workshop
  directory after symlink/``..`` resolution (absolute paths follow the same
  convention as ``read_file`` and are resolved before use).
- **Git-aware corpus**: inside a git work tree the corpus is
  ``git ls-files --cached --others --exclude-standard`` — tracked *and*
  untracked files, honoring ``.gitignore`` (``git ls-files`` alone would
  hide untracked sources, which a development agent must be able to find).
  With ``respect_gitignore=false`` the ignore rules are dropped.
- **Bounded everything**: files scanned, output bytes, match count, and a
  wall-clock budget per call.
- **ReDoS is enforced, not hoped**: literal substring matching is the
  default; regex mode runs a restricted-pattern policy AND the Python
  scanner runs in a separate worker process the parent hard-kills at the
  deadline (an in-thread timer cannot interrupt one pathological pattern).
- ``rg`` (ripgrep), when found on PATH, is a local implementation utility
  only — never assumed present; the Python worker path is always tested.

Pattern semantics for BOTH backends: ``fnmatch`` against the path relative
to the search root (``*`` spans separators, so ``*.py`` matches at any
depth).
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from navig.agent.tools.file_tools import _active_cwd
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Bounds
# ─────────────────────────────────────────────────────────────

#: Default / hard caps for returned matches.
_MAX_MATCHES_DEFAULT = 200
_MAX_MATCHES_HARD = 1_000

#: Default / hard caps for file_search's ``limit``.
_LIMIT_DEFAULT = 200
_LIMIT_HARD = 1_000

#: Files passed to the Python grep worker (corpus cap for huge trees).
_MAX_GREP_CORPUS = 20_000

#: Per-file size ceiling for the content scan.
_MAX_FILE_BYTES = 1_048_576

#: Hard wall-clock budget per search call (seconds).  Read at call time so
#: tests can tighten it.
_WALL_CLOCK_S = 10.0

#: git plumbing timeout (seconds).
_GIT_TIMEOUT_S = 10.0

#: Walk budgets for the non-git fallback.
_WALK_SCANNED_HARD = 100_000

#: Directories never descended into (build/VCS/cache noise).
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".eggs",
    }
)

#: Drive-relative paths ("C:foo") are rejected: joining them onto the active
#: cwd silently jumps to another drive's per-drive cwd.
_DRIVE_RELATIVE_RE = re.compile(r"^[A-Za-z]:")

#: rg output line: ``path:lineno:text`` (first two colons only).
_RG_LINE_RE = re.compile(r"^(.*?):(\d+):(.*)$")

#: Restricted regex policy (defense-in-depth; the worker-process kill is the
#: hard enforcement).  Rejects backreferences, over-long patterns, and
#: quantified groups that themselves contain a quantifier — the classic
#: catastrophic-backtracking shapes like ``(a+)+``.
_MAX_REGEX_LEN = 200


def _validate_regex(pattern: str) -> str | None:
    """Return an error message if *pattern* fails the restricted policy."""
    if len(pattern) > _MAX_REGEX_LEN:
        return f"regex longer than {_MAX_REGEX_LEN} characters — shorten it or use literal=true"
    if re.search(r"\\[1-9]", pattern):
        return "backreferences (\\1..\\9) are not allowed — use literal=true"
    # Scan groups: if a group body contains a quantifier AND the group is
    # itself quantified, refuse.
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "(":
            depth = 1
            j = i + 1
            body_start = j
            while j < len(pattern) and depth:
                if pattern[j] == "\\":
                    j += 2
                    continue
                if pattern[j] == "(":
                    depth += 1
                elif pattern[j] == ")":
                    depth -= 1
                j += 1
            if depth:  # unbalanced — let re.compile decide
                break
            body = pattern[body_start : j - 1]
            after = pattern[j : j + 2]
            body_has_quant = re.search(r"(?<!\\)[+*]|\{\d*,?\d*\}", body) is not None
            quantified_after = after[:1] in ("+", "*") or (
                after[:1] == "{" and re.match(r"\{\d*(,)?\d*\}", after) is not None
            )
            if body_has_quant and quantified_after:
                return (
                    "nested quantifier (a quantified group containing a quantifier) "
                    "is not allowed — rewrite the pattern or use literal=true"
                )
            i = j
            continue
        i += 1
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"invalid regex: {exc}"
    return None


# ─────────────────────────────────────────────────────────────
# Path / corpus helpers
# ─────────────────────────────────────────────────────────────


def _resolve_search_root(raw_path: str | None) -> tuple[Path | None, str | None]:
    """Resolve and validate the search root.

    Returns ``(root, None)`` on success or ``(None, error_message)``.
    Relative paths must remain under the active workshop directory after
    resolution; absolute paths are accepted (same convention as
    ``read_file``) but are still resolved so symlinks cannot redirect the
    walk silently.
    """
    base = _active_cwd()
    raw = (raw_path or "").strip()
    if raw and _DRIVE_RELATIVE_RE.match(raw) and not Path(raw).expanduser().is_absolute():
        return None, f"Drive-relative paths are not allowed: {raw!r} — use an absolute path"

    # Relative (or empty) paths resolve against the SAME active-workshop cwd
    # the escape check below compares to — one source, so a test that
    # rebinds ``_active_cwd`` on this module sees a consistent view.
    raw_p = Path(raw).expanduser() if raw else None
    if raw_p is None:
        candidate = base
    elif raw_p.is_absolute():
        candidate = raw_p
    else:
        candidate = base / raw_p
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        return None, f"Cannot resolve path: {exc}"

    if raw and not Path(raw).expanduser().is_absolute():
        try:
            resolved.relative_to(base.resolve())
        except ValueError:
            return None, f"Path escapes the active workspace: {raw!r} resolves outside {base}"

    if not resolved.exists():
        return None, f"Path not found: {resolved}"
    if not resolved.is_dir():
        return None, f"Path is not a directory: {resolved}"
    return resolved, None


def _git_ls_files(root: Path, respect_gitignore: bool) -> list[str] | None:
    """Tracked + untracked file corpus via ``git ls-files``.

    Returns paths relative to *root* (posix separators), or ``None`` when
    *root* is not inside a git work tree or git failed/timed out — the
    caller then falls back to a bounded walk.
    """
    args = ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others"]
    if respect_gitignore:
        args.append("--exclude-standard")
    try:
        cp = subprocess.run(
            args,
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0:
        return None
    raw = cp.stdout.decode("utf-8", errors="replace")
    return [p for p in raw.split("\0") if p]


class _WalkBudget:
    """Mutable walk accounting: scanned count, deadline, truncation flags."""

    __slots__ = ("scanned", "deadline", "hit_scanned_cap", "hit_deadline")

    def __init__(self, deadline: float) -> None:
        self.scanned = 0
        self.deadline = deadline
        self.hit_scanned_cap = False
        self.hit_deadline = False

    def tick(self) -> bool:
        """Count one entry; return False when the walk must stop."""
        self.scanned += 1
        if self.scanned % 256 == 0 and time.monotonic() > self.deadline:
            self.hit_deadline = True
            return False
        if self.scanned > _WALK_SCANNED_HARD:
            self.hit_scanned_cap = True
            return False
        return True


def _iter_files(root: Path, budget: _WalkBudget):
    """Yield repo-relative posix paths for files under *root*.

    Never descends into :data:`_SKIP_DIRS` and never follows directory
    symlinks (loop protection).
    """
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if not budget.tick():
                        return
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in _SKIP_DIRS:
                                continue
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            yield Path(entry.path).relative_to(root).as_posix()
                    except OSError:
                        continue
        except PermissionError:
            continue
        except OSError:
            continue


def _fnmatch(rel: str, pattern: str) -> bool:
    """Pattern semantics shared by both backends (see module docstring)."""
    return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(
        rel.rsplit("/", 1)[-1], pattern
    )


# ─────────────────────────────────────────────────────────────
# Process envelope: drain + hard kill (shared by rg and the worker)
# ─────────────────────────────────────────────────────────────


def _stream_lines(
    proc: subprocess.Popen,
    *,
    max_lines: int,
    deadline_s: float,
) -> tuple[list[str], bool]:
    """Drain *proc* stdout on a reader thread; hard-kill at the deadline.

    Returns ``(lines, timed_out)``.  The reader stops (and kills the
    process) once *max_lines* lines were collected, so a runaway producer
    cannot exhaust memory.  The wait happens on THIS thread, so a worker
    stuck mid-line cannot block us — the deadline is enforced by kill.
    """
    lines: list[str] = []
    stop = threading.Event()

    def _reader() -> None:
        try:
            if proc.stdout is None:
                return
            for raw in proc.stdout:
                if stop.is_set():
                    break
                lines.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
                if len(lines) >= max_lines:
                    break
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    timed_out = False
    try:
        proc.wait(timeout=deadline_s)
    except subprocess.TimeoutExpired:
        timed_out = True
    except OSError:
        pass

    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    stop.set()
    reader.join(timeout=2)
    return lines, timed_out


def _no_window_kwargs() -> dict[str, Any]:
    """Windows: never flash a console for our child processes."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


# ─────────────────────────────────────────────────────────────
# Python worker source (runs in a separate interpreter, hard-killed
# by the parent at the deadline — see module docstring on ReDoS)
# ─────────────────────────────────────────────────────────────

_WORKER_SRC = r"""
import json, os, re, sys

# Windows text-mode stdout would translate our \n terminators to \r\n and the
# parent would keep the stray \r in match text — write bytes as-is.
try:
    sys.stdout.reconfigure(encoding="utf-8", newline="")
except Exception:
    pass

cfg = json.load(sys.stdin)
root = cfg["root"]
query = cfg["query"]
literal = cfg["literal"]
files = cfg["files"]
limit = cfg["limit"]

try:
    matcher = None if literal else re.compile(cfg.get("pattern", ""))
except Exception:
    sys.exit(3)

emitted = 0
for rel in files:
    if emitted >= limit:
        break
    path = os.path.join(root, rel.replace("/", os.sep))
    try:
        st = os.stat(path)
        if st.st_size > cfg.get("max_bytes", 1048576):
            continue
        with open(path, "rb") as fh:
            head = fh.read(8192)
            if b"\x00" in head:
                continue  # binary
            data = head + fh.read()
    except OSError:
        continue
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), start=1):
        hit = (query in line) if literal else (matcher.search(line) is not None)
        if hit:
            sys.stdout.write(rel + "\t" + str(lineno) + "\t" + line + "\n")
            sys.stdout.flush()
            emitted += 1
            if emitted >= limit:
                break
"""


def _run_python_worker(
    root: Path,
    files: list[str],
    query: str,
    literal: bool,
    pattern: str,
    limit: int,
    deadline_s: float,
) -> tuple[list[str], bool]:
    """Run the content scan in an isolated interpreter with a hard kill."""
    payload = json.dumps(
        {
            "root": str(root),
            "files": files,
            "query": query,
            "literal": literal,
            "pattern": pattern,
            "limit": limit,
            "max_bytes": _MAX_FILE_BYTES,
        }
    )
    try:
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-I", "-c", _WORKER_SRC],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **_no_window_kwargs(),
        )
    except OSError as exc:
        logger.debug("repo_search worker spawn failed: %s", exc)
        return [], False
    try:
        proc.stdin.write(payload.encode("utf-8"))
        proc.stdin.close()
    except OSError:
        pass
    return _stream_lines(proc, max_lines=limit, deadline_s=deadline_s)


def _run_rg(
    root: Path,
    rg: str,
    query: str,
    literal: bool,
    limit: int,
    deadline_s: float,
    respect_gitignore: bool = True,
) -> tuple[list[str], bool, bool]:
    """Run ripgrep as a local utility with the same envelope as the worker.

    Returns ``(lines, timed_out, failed)``.  ``failed`` means rg itself errored
    (spawn failure, or exit status 2 with nothing found — e.g. a flag this rg
    build rejects): the caller must fall back to the Python worker instead of
    reporting "no matches", because an rg *error* is not an empty result.
    The wall-clock deadline is enforced by the parent's hard kill — ripgrep has
    no ``--timeout`` flag, and passing one made every call exit 2 (silently,
    with stderr discarded) until this was caught.
    """
    cmd = [
        rg,
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        "--no-messages",
        "--hidden",
    ]
    if not respect_gitignore:
        cmd.append("--no-ignore")
    if literal:
        cmd.append("--fixed-strings")
    cmd += ["-e", query, "."]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **_no_window_kwargs(),
        )
    except OSError as exc:
        logger.debug("repo_search rg spawn failed: %s", exc)
        return [], False, True
    lines, timed_out = _stream_lines(proc, max_lines=limit, deadline_s=deadline_s)
    # exit 0 = matches, 1 = none, 2 = error. A reader that stopped early at the
    # match cap kills rg (negative/other status) — that is success, not failure.
    failed = proc.returncode == 2 and not lines and not timed_out
    return lines, timed_out, failed


# ─────────────────────────────────────────────────────────────
# Tool: file_search
# ─────────────────────────────────────────────────────────────


class FileSearchTool(BaseTool):
    """Glob search for files under a directory (read-only)."""

    name = "file_search"
    description = (
        "Find files by glob pattern under a directory (e.g. '*.py', 'src/**'. "
        "Returns matching paths relative to the search root, one per line. "
        "Read-only; inside a git repository it honors .gitignore and still "
        "includes untracked files."
    )
    owner_only = False
    parameters = [
        {
            "name": "pattern",
            "type": "string",
            "description": "Glob pattern matched against the path relative to the root (e.g. '*.py')",
            "required": True,
        },
        {
            "name": "path",
            "type": "string",
            "description": "Directory to search (default: active workspace directory)",
            "required": False,
        },
        {
            "name": "limit",
            "type": "integer",
            "description": f"Maximum matches to return (default {_LIMIT_DEFAULT}, max {_LIMIT_HARD})",
            "required": False,
        },
        {
            "name": "respect_gitignore",
            "type": "boolean",
            "description": "Honor .gitignore inside git repositories (default: true)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        pattern = (args.get("pattern") or "").strip()
        if not pattern:
            return ToolResult(name=self.name, success=False, error="'pattern' arg is required")

        root, err = _resolve_search_root(args.get("path"))
        if err:
            return ToolResult(
                name=self.name,
                success=False,
                error=err,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        if root is None:
            return ToolResult(
                name=self.name,
                success=False,
                error=err or f"Cannot resolve path: {args.get('path')!r}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        try:
            limit = int(args.get("limit") or _LIMIT_DEFAULT)
        except (TypeError, ValueError):
            limit = _LIMIT_DEFAULT
        limit = max(1, min(limit, _LIMIT_HARD))
        respect = bool(args.get("respect_gitignore", True))
        deadline = time.monotonic() + _WALL_CLOCK_S

        corpus = _git_ls_files(root, respect)
        source = "git"
        budget = _WalkBudget(deadline)
        if not corpus:
            # None = not a git work tree; [] = git covered nothing here. The
            # empty case happens when the explicitly-searched directory itself
            # sits under an ignored path (e.g. .dev/) or in an empty repo —
            # returning "no files" over files that plainly exist would break
            # the tool's core promise, so walk the tree directly instead.
            corpus = list(_iter_files(root, budget))
            source = "walk"

        matches: list[str] = []
        truncated = False
        for rel in corpus:
            if _fnmatch(rel, pattern):
                matches.append(rel)
                if len(matches) >= limit:
                    truncated = True
                    break
        matches.sort()

        notes: list[str] = []
        if truncated:
            notes.append(f"more matches exist — raise 'limit' (max {_LIMIT_HARD})")
        if budget.hit_deadline:
            notes.append(f"time budget ({_WALL_CLOCK_S:g}s) reached — results are partial")
        if budget.hit_scanned_cap:
            notes.append(f"scan capped at {_WALK_SCANNED_HARD:,} entries — narrow the path")
        if source == "git" and len(corpus) > 0:
            notes.append(f"corpus: git ls-files ({len(corpus):,} files, ignore={respect})")
        elif source == "walk":
            notes.append(f"corpus: directory walk ({len(corpus):,} files)")

        if not matches:
            detail = f" — {'; '.join(notes)}" if notes else ""
            return ToolResult(
                name=self.name,
                success=True,
                output=f'No files matched "{pattern}" in {root}{detail}',
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        body = "\n".join(matches)
        footer = f"{len(matches):,} match(es)"
        if notes:
            footer += " — " + "; ".join(notes)
        return ToolResult(
            name=self.name,
            success=True,
            output=f"{body}\n{footer}",
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


# ─────────────────────────────────────────────────────────────
# Tool: grep_search
# ─────────────────────────────────────────────────────────────


class GrepSearchTool(BaseTool):
    """Content search across files under a directory (read-only)."""

    name = "grep_search"
    description = (
        "Search file contents under a directory. Literal substring by default; "
        "set literal=false for a regular expression (restricted syntax: no "
        "backreferences or nested quantifiers). Returns 'path:line: text' "
        "matches, bounded. Read-only; inside a git repository it honors "
        ".gitignore and still includes untracked files."
    )
    owner_only = False
    parameters = [
        {
            "name": "query",
            "type": "string",
            "description": "Text (or regex when literal=false) to search for",
            "required": True,
        },
        {
            "name": "path",
            "type": "string",
            "description": "Directory to search (default: active workspace directory)",
            "required": False,
        },
        {
            "name": "glob",
            "type": "string",
            "description": "Only search files whose relative path matches this glob (e.g. '*.py')",
            "required": False,
        },
        {
            "name": "literal",
            "type": "boolean",
            "description": "Treat query as a literal substring (default: true)",
            "required": False,
        },
        {
            "name": "max_results",
            "type": "integer",
            "description": f"Maximum matches (default {_MAX_MATCHES_DEFAULT}, max {_MAX_MATCHES_HARD})",
            "required": False,
        },
        {
            "name": "respect_gitignore",
            "type": "boolean",
            "description": "Honor .gitignore inside git repositories (default: true)",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        t0 = time.monotonic()
        query = args.get("query")
        if not isinstance(query, str) or not query:
            return ToolResult(name=self.name, success=False, error="'query' arg is required")
        literal = bool(args.get("literal", True))
        pattern = ""
        if not literal:
            policy_error = _validate_regex(query)
            if policy_error:
                return ToolResult(
                    name=self.name,
                    success=False,
                    error=policy_error,
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                )
            pattern = query

        root, err = _resolve_search_root(args.get("path"))
        if err:
            return ToolResult(
                name=self.name,
                success=False,
                error=err,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        if root is None:
            return ToolResult(
                name=self.name,
                success=False,
                error=err or f"Cannot resolve path: {args.get('path')!r}",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        try:
            max_results = int(args.get("max_results") or _MAX_MATCHES_DEFAULT)
        except (TypeError, ValueError):
            max_results = _MAX_MATCHES_DEFAULT
        max_results = max(1, min(max_results, _MAX_MATCHES_HARD))
        glob = (args.get("glob") or "").strip() or None
        respect = bool(args.get("respect_gitignore", True))
        # Reader cap: one line per match; +1 so truncation is detectable.
        reader_cap = max_results + 1
        deadline_s = _WALL_CLOCK_S
        deadline = time.monotonic() + deadline_s

        rg = shutil.which("rg")
        notes: list[str] = []
        timed_out = False
        raw_lines: list[str] = []

        parsed: list[tuple[str, int, str]] = []
        rg_ok = False
        if rg:
            raw_lines, timed_out, rg_failed = _run_rg(
                root, rg, query, literal, reader_cap, deadline_s, respect
            )
            if rg_failed:
                notes.append("ripgrep errored — fell back to the python worker")
            else:
                rg_ok = True
                notes.append("backend: ripgrep")
                for line in raw_lines:
                    m = _RG_LINE_RE.match(line)
                    if not m:
                        continue
                    # Windows rg prints ".\path"; normalise to repo-relative posix.
                    rel = m.group(1).replace("\\", "/")
                    if rel.startswith("./"):
                        rel = rel[2:]
                    if glob and not _fnmatch(rel, glob):
                        continue
                    parsed.append((rel, int(m.group(2)), m.group(3)))
        if not rg_ok:
            if not rg:
                notes.append("backend: python worker (rg not found)")
            corpus = _git_ls_files(root, respect)
            if not corpus:
                # See FileSearchTool: an empty git result over an
                # explicitly-searched tree means the ignore rules swallowed
                # the root itself — walk directly instead of reporting nothing.
                budget = _WalkBudget(deadline)
                corpus = list(_iter_files(root, budget))
                if budget.hit_deadline:
                    timed_out = True
                    notes.append(f"time budget ({_WALL_CLOCK_S:g}s) reached while listing files")
                if budget.hit_scanned_cap:
                    notes.append(f"scan capped at {_WALK_SCANNED_HARD:,} entries")
            if glob:
                corpus = [rel for rel in corpus if _fnmatch(rel, glob)]
            if len(corpus) > _MAX_GREP_CORPUS:
                notes.append(
                    f"corpus capped at {_MAX_GREP_CORPUS:,} of {len(corpus):,} files "
                    "— narrow the path or glob"
                )
                corpus = corpus[:_MAX_GREP_CORPUS]
            if corpus and not timed_out:
                worker_lines, worker_timeout = _run_python_worker(
                    root, corpus, query, literal, pattern, reader_cap, deadline_s
                )
                timed_out = timed_out or worker_timeout
                parsed = []
                for line in worker_lines:
                    parts = line.split("\t", 2)
                    if len(parts) != 3:
                        continue
                    rel, lineno, text = parts
                    try:
                        parsed.append((rel, int(lineno), text))
                    except ValueError:
                        continue
            else:
                parsed = []

        truncated = len(parsed) > max_results
        if truncated:
            parsed = parsed[:max_results]

        if timed_out:
            notes.append(f"hard deadline ({deadline_s:g}s) reached — results are partial")

        if not parsed:
            elapsed = (time.monotonic() - t0) * 1000
            if timed_out:
                return ToolResult(
                    name=self.name,
                    success=False,
                    error=(
                        f"Search timed out after {deadline_s:g}s before finding a match — "
                        "narrow the path, use a literal query, or add a glob filter"
                    ),
                    elapsed_ms=elapsed,
                )
            detail = f" — {'; '.join(notes)}" if notes else ""
            return ToolResult(
                name=self.name,
                success=True,
                output=f'No matches for {query!r} in {root}{detail}',
                elapsed_ms=elapsed,
            )

        parsed.sort(key=lambda item: (item[0], item[1]))
        body_lines = [f"{rel}:{lineno}: {text}" for rel, lineno, text in parsed]
        footer = f"{len(parsed):,} match(es)"
        if truncated:
            footer += f" — truncated at {max_results:,} (raise 'max_results' up to {_MAX_MATCHES_HARD:,})"
        if notes:
            footer += " — " + "; ".join(notes)
        return ToolResult(
            name=self.name,
            success=True,
            output="\n".join(body_lines) + "\n" + footer,
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )


def register_repo_search_tools() -> None:
    """Register ``file_search`` and ``grep_search`` (toolset ``code``)."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(FileSearchTool(), toolset="code")
    _AGENT_REGISTRY.register(GrepSearchTool(), toolset="code")
    logger.debug("Agent repo search tools registered: file_search, grep_search")
