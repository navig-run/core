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
from collections import defaultdict
from pathlib import Path

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
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — a template/sample that isn't valid python
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            declared[node.name] += 1  # every class, dataclass or not — shadowing is ambiguity
            if _is_dataclass_def(node):
                if node.bases:
                    inherited.add(node.name)
                specs[node.name].append(_field_spec(node))
    return {
        name: found[0]
        for name, found in specs.items()
        if len(found) == 1 and declared[name] == 1 and name not in inherited
    }


def _visible_names(tree: ast.AST) -> set[str]:
    """Names imported or defined in this file — so we only judge a construction we can resolve."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {(a.asname or a.name) for a in node.names}
        elif isinstance(node, ast.Import):
            names |= {(a.asname or a.name.split(".")[0]) for a in node.names}
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
    return names


def _scan() -> list[str]:
    models = _collect_dataclasses()
    findings: list[str] = []
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        visible = _visible_names(tree)
        rel = path.relative_to(CORE.parent).as_posix()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            spec = models.get(node.func.id)
            if spec is None or node.func.id not in visible:
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
    visible = _visible_names(planted)
    allowed, required = models["WindowInfo"]
    call = next(
        n for n in ast.walk(planted)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "WindowInfo"
    )
    kwargs = {k.arg for k in call.keywords if k.arg}
    assert "WindowInfo" in visible
    assert kwargs - allowed == {"bogus"}  # unknown kwarg caught
    assert "class_name" in required - kwargs  # missing required caught
