"""Git-checkout detection must never use `(x / ".git").exists()` again.

`(src_dir / ".git").exists()` answers "is `.git` a direct child of *src_dir*" — which is
False the moment *src_dir* is a repo SUBDIRECTORY, and (in this monorepo) it is ALSO False
for navig's own source because `.git` lives at the repo ROOT, not inside `core/`. That one
assumption made `navig update` / `navig upgrade` / the update checker silently take the pip
path and NEVER pull the editable install — the root cause behind the whole "merged but not
live" saga (#521 → #533 → #536), and it made `navig context init` skip gitignoring `.navig/`
from a subdir (#539). The correct detection is:

  * navig's OWN source  → `navig.commands.update._is_navig_git_checkout` (`git ls-files
    --error-unmatch navig/__init__.py` — True only when git actually tracks navig here);
  * a user's repo       → `git rev-parse --show-toplevel` (walks UP to the repo root).

This guard is the lock on that door. It flags any NEW `(x / ".git").exists()` / `.is_dir()`
CALL in `core/navig`. The remaining legitimate uses — walk-UP loops (which stop at the first
`.git` and so correctly find the root from any depth) and checks of a path that IS a repo
root/clone by construction — are an explicit ALLOWLIST. A new detection that reaches for the
trap fails the build with a pointer to the right helper.

Precision over reach: the detector matches the AST *call*, so the same string in the
docstrings/comments that fixed files use to WARN about the anti-pattern is not flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Locate sources by path, never import them (fast; usable as a pre-push gate even when the
# tree does not import cleanly). core/tests/quality/<this> -> parents[2] == core.
_CORE_ROOT = Path(__file__).resolve().parents[2] / "navig"

_EXCLUDE_DIRS = {"tests", "test", ".venv", "node_modules", ".dev", "build", "dist", "__pycache__"}

# The ONLY places `(x / ".git").exists()` is correct. Keyed "navig/<relpath>" -> reason.
# Every entry is proven still-live by test_allowlist_has_no_stale_entries.
_ALLOWLIST: dict[str, str] = {
    # Walk-UP loops: iterate [dir, *dir.parents] and stop at the first `.git`, so they
    # correctly find the repo root from ANY depth — the right pattern, not the trap.
    "navig/agent/tools/git_tools.py": "_find_git_root walks up the parents",
    "navig/guard/agent_lock.py": "repo-root discovery walks up the parents",
    "navig/guard/session_start.py": "repo-root discovery walks up the parents",
    # Checks of a path that IS a repo root / clone / worktree by construction (never a
    # possible subdirectory) — 'is THIS dir a git repo' is the correct question there.
    "navig/commands/contribute.py": "checks a freshly-cloned repo root",
    "navig/commands/repo.py": "checks a worktree checkout dir",
    "navig/commands/project_inspect.py": "reports has_git for the inspected project root",
    "navig/selfheal/git_manager.py": "checks the managed clone dir (~/.navig/core-repo)",
}


# ── detection ───────────────────────────────────────────────────────────────


def _git_exists_calls(tree: ast.AST) -> list[int]:
    """Line numbers of `(X / ".git").exists()` / `.is_dir()` CALLS in *tree*.

    Matches the AST call only, so the anti-pattern spelled out in a comment or docstring
    (as the fixed files do, to warn readers) is never a false positive.
    """
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("exists", "is_dir")):
            continue
        obj = fn.value  # the receiver of .exists()/.is_dir()
        if (
            isinstance(obj, ast.BinOp)
            and isinstance(obj.op, ast.Div)
            and isinstance(obj.right, ast.Constant)
            and obj.right.value == ".git"
        ):
            out.append(node.lineno)
    return out


def _scan() -> dict[str, list[int]]:
    """{ 'navig/<rel>': [linenos] } for every core file with a `(X/".git").exists()` call."""
    found: dict[str, list[int]] = {}
    for py in _CORE_ROOT.rglob("*.py"):
        rel = py.relative_to(_CORE_ROOT)
        if set(rel.parts) & _EXCLUDE_DIRS:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        lines = _git_exists_calls(tree)
        if lines:
            found[f"navig/{rel.as_posix()}"] = lines
    return found


# ── the guard ───────────────────────────────────────────────────────────────


def test_no_unlisted_dotgit_exists_detection():
    """No NEW `(x / ".git").exists()` git detection — it is False from a subdir and when
    `.git` is at the repo root. Everything remaining is an allowlisted walk-up / known-root."""
    offenders = [
        f"{rel}:{ln}"
        for rel, lines in _scan().items()
        if rel not in _ALLOWLIST
        for ln in lines
    ]
    assert not offenders, (
        "git-checkout detection via `(x/'.git').exists()` reintroduced — it is False from a "
        "repo subdirectory and when `.git` is at the repo root (this monorepo). Use "
        "`navig.commands.update._is_navig_git_checkout` for navig's own source, or "
        "`git rev-parse --show-toplevel` for a user repo — or add an ALLOWLIST entry with a "
        "reason if the path is a repo root by construction:\n  " + "\n  ".join(offenders)
    )


def test_allowlist_has_no_stale_entries():
    """Every ALLOWLIST file must still exist AND still contain a `(x/".git").exists()` call —
    a stale entry rots the guard into a blind spot."""
    found = _scan()
    stale = [rel for rel in _ALLOWLIST if rel not in found]
    assert not stale, (
        "stale ALLOWLIST entries (file gone or no longer uses `(x/'.git').exists()`) — "
        f"remove them: {stale}"
    )


# ── the detector must keep its teeth (a blind guard is worse than none) ───────


def test_guard_flags_a_new_dotgit_exists_call():
    src = "from pathlib import Path\ndef f(p):\n    return (p / '.git').exists()\n"
    assert _git_exists_calls(ast.parse(src)) == [3]


def test_guard_flags_is_dir_too():
    src = "def f(p):\n    return (p / '.git').is_dir()\n"
    assert _git_exists_calls(ast.parse(src)) == [2]


def test_guard_ignores_the_helper_and_rev_parse():
    src = (
        "def f(p):\n"
        "    from navig.commands.update import _is_navig_git_checkout\n"
        "    return _is_navig_git_checkout(p)\n"
    )
    assert _git_exists_calls(ast.parse(src)) == []


def test_guard_ignores_the_pattern_in_a_docstring():
    # The fixed files describe the anti-pattern in prose — a string, not a call.
    src = 'def f():\n    """NOT (src / \\".git\\").exists() — use rev-parse."""\n    return 1\n'
    assert _git_exists_calls(ast.parse(src)) == []
