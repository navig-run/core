"""Guard: every ``from navig.<x> import ...`` must name a module that exists.

Any scope. Guarded or not. Across core, every plugin, and harbor.

Why this is not already covered
-------------------------------
``test_no_dangling_module_imports.py`` scans ``core/navig`` only, and only
MODULE-LEVEL imports that are not wrapped in ``try/except``. Both exclusions are
correct for what that guard checks (a name missing from a module that does exist),
and both are exactly wrong for this class. Six live defects sat in the blind spot:

  * ``navig-social`` and ``private/harbor`` both imported ``navig.llm_generate``.
    That module has never existed — it is ``navig.llm.generate``. Studio's AI
    endpoint returned 502 on every request; harbor's AI spending suggestions were
    swallowed at DEBUG and never once ran.
  * ``navig.commands.email`` in the task bridge — the email provider always failed,
    and told the user to install a plugin that would not have helped.
  * ``navig.daemon_client`` in a deck route — neither module nor function ever
    existed, so the DevOps panel always rendered fallback data.
  * ``navig.gateway.media`` in the Telegram command router, and
    ``navig.bizops.briefing`` in the briefing chain.

The rule is narrow on purpose: ``navig.*`` is FIRST-PARTY. It is never an optional
dependency of navig's own code, so a ``try/except ImportError`` around it cannot be
protecting an optional install — it can only be converting a permanent typo into a
silent feature outage. (A plugin that genuinely runs without core routes its core
touches through a ``_compat`` module; see ``navig_blackbox/_compat.py``.)

Why resolution is done on the file tree, not by importing
---------------------------------------------------------
Two methods were measured against this tree and BOTH are wrong:

  * Disk paths requiring ``__init__.py`` report a false positive for
    ``navig.gateway.deck.routes`` — a PEP 420 namespace package that imports fine.
  * ``importlib.util.find_spec`` imports parent packages, so a missing THIRD-PARTY
    dependency masquerades as a dangling first-party import: it reported 46
    findings here, 42 of them ``navig.tui.*`` files that plainly exist and fail
    only because ``textual`` is not installed.

The hybrid below — a module is a ``.py`` file OR a directory — reported exactly the
four real core findings, and executes nothing.

Relative imports are deliberately out of scope
----------------------------------------------
Measured: 420 relative imports across core + plugins + harbor + registry, ONE of
which does not resolve — ``core/navig/core/kernel.py``'s ``.plugin_manager``, a
deliberate legacy shim whose ``except ImportError`` installs a documented stub class
("navig.core.plugin_manager was removed; NavigKernel is unused"). That is a correct
use of a guarded import, so there is nothing here to gate. Recorded so the next
person does not re-derive it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
NAVIG = REPO / "core" / "navig"

# Every tree that may import navig. `plugins/` and `private/harbor` are the point:
# the class was found there, and the existing guard cannot see them.
_SCAN_ROOTS = (
    "core/navig",
    "core/tools",
    "plugins",
    "private",
    "apps",
    "registry",
)

_SKIP_PARTS = {"__pycache__", "build", "dist", ".venv", "node_modules", ".lab", ".dev"}


def _is_skipped(path: Path) -> bool:
    """Is this file inside a directory we never scan?

    Matched against the path RELATIVE TO ``REPO``, never the absolute path: the repo
    itself can live under a directory whose name is in the skip set. A worktree at
    ``.dev/worktrees/<slug>`` -- the sanctioned parallel-work path in this repo -- is
    exactly that case, and matching absolute parts skipped every file there: 0 scanned
    instead of ~1850, so the guard verified nothing at all. The floor assertion below
    is what caught it; this keeps the skip meaning "``.dev`` INSIDE the repo".
    """
    try:
        rel = path.relative_to(REPO)
    except ValueError:  # outside the repo entirely -- not ours to scan
        return True
    return bool(_SKIP_PARTS & set(rel.parts))

# (file, module) -> reason. An entry asserts the import is CORRECT and this guard is
# wrong about it; it is not a parking space for a known-broken import. Empty today.
_BASELINE: dict[tuple[str, str], str] = {}


def _resolves(dotted: str) -> bool:
    """True when `dotted` names a real module under core/navig.

    A directory counts even without ``__init__.py`` — PEP 420 namespace packages are
    importable, and treating them as missing is how the first version of this guard
    produced a false positive.
    """
    parts = dotted.split(".")
    if parts[0] != "navig":
        return True
    target = NAVIG.joinpath(*parts[1:])
    return target.with_suffix(".py").is_file() or target.is_dir()


def _imported_navig_modules(tree: ast.AST) -> list[tuple[int, str]]:
    """(lineno, dotted) for every ABSOLUTE navig import, at any nesting depth."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue  # relative — see the module docstring
            found.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
    return [(ln, m) for ln, m in found if m == "navig" or m.startswith("navig.")]


def test_every_navig_import_names_a_real_module() -> None:
    offenders: list[str] = []
    scanned = 0

    for root in _SCAN_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue  # a checkout without private/ simply contributes nothing
        for f in sorted(base.rglob("*.py")):
            if _is_skipped(f):
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            scanned += 1
            rel = f.relative_to(REPO).as_posix()
            for lineno, module in _imported_navig_modules(tree):
                if _resolves(module) or (rel, module) in _BASELINE:
                    continue
                offenders.append(
                    f"{rel}:{lineno} imports `{module}` — no such module under core/navig. "
                    f"navig.* is first-party and never optional, so a try/except around this "
                    f"hides a permanent failure rather than handling a missing install."
                )

    # A scan that silently reads nothing looks exactly like a clean run. The floor is well
    # under the ~1850 files present, so it fails on a moved tree rather than on growth.
    assert scanned > 1200, (
        f"only {scanned} files scanned across {_SCAN_ROOTS} — the tree moved and this guard "
        f"verified almost nothing. Point _SCAN_ROOTS at the new layout."
    )

    assert not offenders, "\n".join(
        [f"{len(offenders)} unresolvable first-party import(s):", *offenders]
    )


# ── The skip set is repo-RELATIVE (regression) ───────────────────────────────
# `.dev` is in _SKIP_PARTS to skip `.dev/worktrees` INSIDE the repo. Matched against
# the ABSOLUTE path it also skipped the repo itself whenever the checkout lived under
# such a directory -- which is every `.dev/worktrees/<slug>` worktree, the sanctioned
# parallel-work path here. Result: 0 files scanned instead of ~1850. Only the floor
# assertion above stood between that and a guard that passes having read nothing.


def test_skip_set_is_matched_relative_to_the_repo(tmp_path, monkeypatch):
    """A repo that itself sits under a skipped directory name is still scanned."""
    import tests.quality.test_no_unresolvable_navig_module as mod

    fake_repo = tmp_path / ".dev" / "worktrees" / "slug"
    (fake_repo / "core" / "navig").mkdir(parents=True)
    inside = fake_repo / "core" / "navig" / "thing.py"
    inside.write_text("x = 1\n", encoding="utf-8")
    skipped = fake_repo / "core" / "navig" / "__pycache__" / "thing.py"
    skipped.parent.mkdir()
    skipped.write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setattr(mod, "REPO", fake_repo)
    assert mod._is_skipped(inside) is False, (
        "a file under a repo whose own path contains '.dev' must still be scanned"
    )
    assert mod._is_skipped(skipped) is True, "'__pycache__' inside the repo is still skipped"
    assert mod._is_skipped(tmp_path / "elsewhere.py") is True, "outside the repo is not ours"


def test_the_real_scan_reaches_the_tree_it_guards():
    """Teeth for the floor: the live REPO resolves to a tree with files in it."""
    found = [f for f in (REPO / "core" / "navig").rglob("*.py") if not _is_skipped(f)]
    assert len(found) > 500, f"only {len(found)} files under core/navig — REPO is wrong"
