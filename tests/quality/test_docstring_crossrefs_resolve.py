"""A ``:func:`X``` in a docstring must name something that exists.

A cross-reference is a promise that the reader can go and check the claim. When
the name is wrong the reader finds nothing, and — worse — the surrounding
sentence is usually the *explanation of a subtle behaviour*, so the one place
that needed verifying is the one place that cannot be.

Measured across 1,545 files when this landed: **three** bare, project-shaped
references naming something that exists nowhere in the tree.

* ``commands/gateway.py`` credited ``_record_gateway_pid`` with writing the pid
  file. The writer is ``_write_gateway_pid``. That sentence is the record of a
  safety fix — `gateway stop` reading a hardcoded ``~/.navig/gateway.pid`` and
  `taskkill /F`-ing the operator's live daemon — so the reference someone would
  follow to confirm "the writer really does use config_dir()" led nowhere.
* ``connectors/gmail/oauth_config.py`` said ``GMAIL_OAUTH_CONFIG`` is registered
  by ``register_gmail_oauth``. **Neither name exists.** The real path is
  ``get_gmail_oauth_config`` → ``connectors.bootstrap`` → ``register_provider``.
* ``agent/delegate.py`` claimed a caller for ``register_delegate_tool`` that has
  never existed (fixed separately, and pinned by ``test_dormant_modules.py``).

Scope is **bare names only**, and the reason is measurement rather than
difficulty. This file first justified the limit by saying dotted targets "need an
import graph" — that was wrong, and it is the same shape of unchecked claim the
guard exists to catch, so it is corrected here rather than quietly dropped.
Both dotted categories resolve without one, and both were then measured clean:

* **project-qualified** (``navig_social.social.publishers.FacebookPagePublisher``)
  — map the dotted module onto its file and AST-read that module's top level. No
  imports, no side effects. Across 2,965 mapped modules: **0 missing**.
* **stdlib-qualified** (``yaml.safe_load``, ``functools.partial``,
  ``hmac.compare_digest``) — ``importlib`` plus ``getattr``. 8 references
  resolved, **0 missing**.

So the narrow scope costs nothing today. It stays narrow because the extra
machinery would guard an empty set, and a guard earns its complexity from hits —
the same reason there is no guard for "exception caught but never raised" (2
tree-wide, both false positives). Re-measure before widening: if either dotted
category ever grows a real hit, that is the signal to extend this.
"""

from __future__ import annotations

import ast
import builtins
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_REF = re.compile(r":(?:func|meth|class|data|attr|exc):`~?([A-Za-z_][\w.]*)`")


def _roots() -> list[Path]:
    out = [_ROOT / "core" / "navig"]
    out += sorted((_ROOT / "plugins").glob("navig-*/navig_*"))
    return [p for p in out if p.is_dir()]


def _defined_names(tree: ast.AST) -> set[str]:
    """Everything a bare reference could plausibly resolve to in this module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            # `from x import Y as Z` — the alias is what a reference would use.
            names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


@pytest.fixture(scope="module")
def scan() -> tuple[dict[str, set[str]], int]:
    """(file -> unresolvable bare refs, files scanned)."""
    parsed: dict[Path, ast.AST] = {}
    everywhere: set[str] = set()
    for root in _roots():
        for path in root.rglob("*.py"):
            if "build" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            parsed[path] = tree
            everywhere |= _defined_names(tree)

    bad: dict[str, set[str]] = {}
    for path, tree in parsed.items():
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            doc = ast.get_docstring(node)
            if not doc:
                continue
            for target in _REF.findall(doc):
                if "." in target or hasattr(builtins, target):
                    continue
                if target not in everywhere:
                    rel = path.relative_to(_ROOT).as_posix()
                    bad.setdefault(rel, set()).add(target)
    return bad, len(parsed)


def test_the_scan_actually_read_the_tree(scan):
    """A scan that reads nothing passes every assertion below."""
    _bad, count = scan
    assert count > 1000, f"only parsed {count} files — the roots are wrong"


def test_every_bare_docstring_reference_resolves(scan):
    bad, _count = scan
    assert not bad, (
        "docstring cross-references naming something that exists nowhere in the "
        "tree — a reader following these to check the claim finds nothing:\n  "
        + "\n  ".join(f"{f}: {sorted(v)}" for f, v in sorted(bad.items()))
    )
