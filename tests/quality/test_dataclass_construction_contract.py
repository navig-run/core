"""Every dataclass construction in core must match the dataclass's real fields.

The connector sweep (#693/#697) found ten connectors that had never run because they constructed
types that did not exist in that shape — ``Resource(body=…)``, ``ActionResult(data=…)``,
``HealStatus(healthy=…)``. That guard (``test_connector_type_contract.py``) locked the door for
``navig/connectors``. This one widens the same check to ALL of ``core/navig`` — where it
immediately found four more live crashes that no test covered:

* ``ProviderConfig(env_key=…)`` — no such field, and the same ``__init__`` called
  ``super().__init__()`` with no ``config``: ``create_perplexity_client()`` raised ``TypeError``,
  so the whole Perplexity provider was dead on arrival.
* ``HealResult(pr_url=…)`` — the field was missing from the dataclass while BOTH the producer and
  the reply builder (``if result.pr_url``) assumed it, so a heal crashed right after opening a
  real GitHub PR, and every "partial" heal crashed on the read side.
* ``WindowInfo(...)`` without the REQUIRED ``class_name`` in the Linux and macOS automation
  adapters (x4) — ``list_windows``/``get_active_window`` raised ``TypeError`` on those platforms.

This is deliberately a PURE-AST check: it never imports a module, so it is fast (no side effects,
no optional-dependency skips) and can run on every commit. It resolves a constructed name only
when that name is unambiguous tree-wide AND visible in the constructing file, so it reports
nothing it cannot prove.

Scope note: this guard checks CONSTRUCTIONS. The connector guard additionally checks
``self.x()`` resolution and ``Action`` attribute access — those stay connector-scoped because the
gateway's ``*Mixin`` classes legitimately call methods only the composing class provides, which
would make a tree-wide version pure noise.
"""

from __future__ import annotations

import ast
import functools
from collections import defaultdict
from pathlib import Path

import pytest

# Pinned to one of TWO xdist lanes (needs `--dist loadgroup`, which the gate passes):
# this guard parses the whole tree, and eight workers each holding a whole-tree AST
# at the same moment is what crashes workers on a shared box and fakes a red gate.
# Lane assignment is declared in tests/quality/test_heavy_tree_guards_are_pinned.py.
pytestmark = [pytest.mark.xdist_group("whole_tree_ast_2"), pytest.mark.timeout(300)]

CORE = Path(__file__).resolve().parents[2] / "navig"

# Vendored/generated trees that are not navig's own source.
_SKIP_PARTS = {"__pycache__", "scaffold-templates", "templates", "builtin"}


def _sources() -> list[Path]:
    return [p for p in sorted(CORE.rglob("*.py")) if not (_SKIP_PARTS & set(p.parts))]


def _is_dataclass_def(node: ast.ClassDef) -> bool:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = getattr(target, "attr", None) or getattr(target, "id", None)
        if name == "dataclass":
            return True
    return False


def _field_spec(node: ast.ClassDef) -> tuple[set[str], set[str]]:
    """(all constructor field names, names with no default) read straight from the AST."""
    allowed: set[str] = set()
    required: set[str] = set()
    for stmt in node.body:
        if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
            continue
        annotation = ast.unparse(stmt.annotation) if stmt.annotation else ""
        if annotation.startswith("ClassVar"):
            continue  # not a constructor parameter
        name = stmt.target.id
        has_default = stmt.value is not None
        if isinstance(stmt.value, ast.Call):
            fn = stmt.value.func
            fname = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if fname == "field":
                kws = {k.arg: k.value for k in stmt.value.keywords}
                if isinstance(kws.get("init"), ast.Constant) and kws["init"].value is False:
                    continue  # field(init=False) — not a constructor parameter
                has_default = "default" in kws or "default_factory" in kws
        allowed.add(name)
        if not has_default:
            required.add(name)
    return allowed, required


# Cached: three tests ask for it, and each call parses the whole tree (~6 s). The result is a
# small dict of names → field sets; the ASTs are dropped, so this pins no memory.
@functools.lru_cache(maxsize=1)
def _collect_dataclasses() -> dict[str, tuple[set[str], set[str]]]:
    """name -> (allowed, required), for names that unambiguously identify one plain dataclass.

    A name is dropped when it is declared more than once ANYWHERE in core — even by a non-
    dataclass. ``ProbeResult`` is both a ``NamedTuple`` (agent/llm_probe.py) and an unrelated
    dataclass (blocks/policy.py); matching constructions of the first against the fields of the
    second produced eight false reports before this filter existed. A dataclass with a base class
    is dropped too: it inherits fields this AST-only pass cannot see.
    """
    specs: dict[str, list[tuple[set[str], set[str]]]] = defaultdict(list)
    declared: dict[str, int] = defaultdict(int)
    inherited: set[str] = set()
    origin: dict[str, str] = {}
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — a template/sample that isn't valid python
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            declared[node.name] += 1  # every class, dataclass or not — shadowing is ambiguity
            origin.setdefault(node.name, _module_of(path))
            if _is_dataclass_def(node):
                if node.bases:
                    inherited.add(node.name)
                specs[node.name].append(_field_spec(node))
    found = {
        name: spec[0]
        for name, spec in specs.items()
        if len(spec) == 1 and declared[name] == 1 and name not in inherited
    }
    _ORIGINS.clear()
    _ORIGINS.update({name: origin[name] for name in found})
    return found


#: name -> dotted module that defines it, filled by `_collect_dataclasses`.
_ORIGINS: dict[str, str] = {}


def _module_of(path: Path) -> str:
    """The dotted module a core source file provides (`core/navig/x/y.py` -> `navig.x.y`)."""
    rel = path.relative_to(CORE).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(["navig", *parts])


def _visible_names(tree: ast.AST) -> tuple[set[str], dict[str, set[str]]]:
    """(names defined in this file, name -> the modules this file imports it from).

    A construction is judged only when we can prove WHICH class it means. Two names
    collide tree-wide today that nothing is wrong with: `navig.telegram.card_doc`
    defines a `Table`, and a hundred command modules construct rich's
    `console_helper.Table` — same name, different class. Importing a name from a
    module that does not define it is exactly the case this guard cannot judge.
    """
    local: set[str] = set()
    imported: dict[str, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imported[alias.asname or alias.name].add(module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported[alias.asname or alias.name.split(".")[0]].add(alias.name)
        elif isinstance(node, ast.ClassDef):
            local.add(node.name)
    return local, imported


def _imports_from(modules: set[str], defining: str) -> bool:
    """Whether one of the import sources IS the defining module (the `from x import y` case)."""
    return any(mod == defining or mod.endswith("." + defining) or defining.endswith("." + mod)
               for mod in modules if mod)


def _scan() -> list[str]:
    models = _collect_dataclasses()
    findings: list[str] = []
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        local, imported = _visible_names(tree)
        rel = path.relative_to(CORE.parent).as_posix()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            spec = models.get(node.func.id)
            if spec is None:
                continue
            if node.func.id not in local:
                defining = _ORIGINS.get(node.func.id, "")
                if node.func.id not in imported or not _imports_from(imported[node.func.id], defining):
                    continue
            allowed, required = spec
            kwargs = {k.arg for k in node.keywords if k.arg}
            unknown = kwargs - allowed
            if unknown:
                findings.append(
                    f"{rel}:{node.lineno}: {node.func.id}(**{sorted(unknown)}) — no such field"
                )
            # Only judge completeness when every argument is an explicit keyword.
            if not (any(k.arg is None for k in node.keywords) or node.args):
                missing = required - kwargs
                if missing:
                    findings.append(
                        f"{rel}:{node.lineno}: {node.func.id}() missing required {sorted(missing)}"
                    )
    return findings


def test_dataclass_constructions_match_their_definitions():
    findings = _scan()
    assert not findings, (
        "Dataclass constructed with a shape it does not have — this code raises TypeError "
        "when it runs:\n" + "\n".join(f"  {f}" for f in findings)
    )


def test_guard_resolves_a_known_dataclass():
    """Sanity: the collector actually sees core's dataclasses (a silent empty scan would pass)."""
    models = _collect_dataclasses()
    assert len(models) > 50, f"only resolved {len(models)} dataclasses — collector is broken"
    assert "WindowInfo" in models
    allowed, required = models["WindowInfo"]
    assert "class_name" in required  # required-with-no-default, the bug this caught
    assert "is_minimized" in allowed and "is_minimized" not in required  # has a default


def test_guard_detects_planted_violations(tmp_path):
    """Teeth: a bad construction of a real core dataclass is reported."""
    planted = ast.parse(
        "from navig.adapters.automation.types import WindowInfo\n"
        "w = WindowInfo(id='1', title='t', x=0, y=0, width=1, height=1, pid=0, bogus=2)\n"
    )
    models = _collect_dataclasses()
    local, imported = _visible_names(planted)
    allowed, required = models["WindowInfo"]
    call = next(
        n for n in ast.walk(planted)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "WindowInfo"
    )
    kwargs = {k.arg for k in call.keywords if k.arg}
    # Imported from the module that defines it — the case the guard is allowed to judge.
    assert "WindowInfo" not in local
    assert _imports_from(imported["WindowInfo"], _ORIGINS["WindowInfo"])
    assert kwargs - allowed == {"bogus"}  # unknown kwarg caught
    assert "class_name" in required - kwargs  # missing required caught
