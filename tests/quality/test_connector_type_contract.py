"""Every connector must construct the REAL connector dataclasses and call methods that exist.

A connector is the one place in core where nothing fails until a user connects a live account —
so a connector written against a *different* (imagined, or since-changed) shape of the shared
types is never exercised and ships dead. That is not hypothetical: ``supabase`` (#693),
``google_maps``, ``youtube`` and ``gcp_translate`` each crashed on the FIRST line of EVERY method,
and ``navig-calendar`` (#674) called a provider method no provider defined. Five distinct shapes
of the same class:

* ``self._require_connected()`` — called at the top of every request path, **defined nowhere** →
  ``AttributeError`` before any work happens.
* ``Resource(body=…)`` with no ``source`` — the field is ``preview`` and ``source`` is required
  → ``TypeError`` in ``search``/``fetch``.
* ``ActionResult(data=…)`` — there is no ``data`` field → ``TypeError`` on every ``act()``.
* ``HealthStatus(healthy=…)`` / ``HealthStatus(ok=False, message=…)`` — the field is ``ok`` and
  ``latency_ms`` was required → ``TypeError`` exactly when the connector is unhealthy.
* ``action.name`` — ``Action`` has ``action_type``/``params``, never ``name`` → ``AttributeError``.

A unit test can't catch this cheaply (each connector needs its own transport mocked), and mypy is
not run over this tree — but the whole class is statically decidable: compare every construction
against the dataclass's REAL fields, resolved at runtime via ``dataclasses.fields`` so this guard
can never drift from ``connectors/types.py``. Any finding here is a genuine "this code has never
run"; there is deliberately no allowlist.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
from pathlib import Path

from navig.connectors import types as connector_types
from navig.connectors.base import ConnectorManifest

CORE = Path(__file__).resolve().parents[2] / "navig"
CONNECTORS = CORE / "connectors"

# The dataclasses a connector constructs. Fields are read from the live classes, never retyped.
_MODELS: dict[str, type] = {
    "Resource": connector_types.Resource,
    "ActionResult": connector_types.ActionResult,
    "HealthStatus": connector_types.HealthStatus,
    "Action": connector_types.Action,
    "ConnectorManifest": ConnectorManifest,
}


def _field_spec(cls: type) -> tuple[set[str], set[str]]:
    """(all field names, names with no default) for a dataclass."""
    allowed, required = set(), set()
    for f in dataclasses.fields(cls):
        allowed.add(f.name)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:  # type: ignore[misc]
            required.add(f.name)
    return allowed, required


def _connector_sources() -> list[Path]:
    return [p for p in sorted(CONNECTORS.rglob("*.py")) if "__pycache__" not in p.parts]


def _rel(path: Path) -> str:
    return path.relative_to(CORE.parent).as_posix()


def _class_members(module_path: Path, class_name: str) -> set[str]:
    """Members visible on the real class (its own + inherited). Empty if it can't be imported."""
    try:
        rel = module_path.relative_to(CORE)
    except ValueError:  # a path outside core/navig (the planted-violation test) — AST-only
        return set()
    dotted = "navig." + rel.with_suffix("").as_posix().replace("/", ".")
    try:
        mod = importlib.import_module(dotted)
    except Exception:  # pragma: no cover — an optional-dep module still gets AST-only checking
        return set()
    cls = getattr(mod, class_name, None)
    return set(dir(cls)) if cls is not None else set()


def _model_violations(tree: ast.AST, rel: str) -> list[str]:
    """Constructions of a connector dataclass with unknown or missing-required kwargs."""
    out: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        cls = _MODELS.get(node.func.id)
        if cls is None:
            continue
        allowed, required = _field_spec(cls)
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        unknown = kwargs - allowed
        if unknown:
            out.append(f"{rel}:{node.lineno}: {node.func.id}(**{sorted(unknown)}) — no such field")
        # Only judge completeness when every argument is an explicit keyword (no *args/**kwargs).
        splatted = any(kw.arg is None for kw in node.keywords) or bool(node.args)
        if not splatted:
            missing = required - kwargs
            if missing:
                out.append(
                    f"{rel}:{node.lineno}: {node.func.id}() missing required {sorted(missing)}"
                )
    return out


def _self_call_violations(tree: ast.AST, path: Path, rel: str) -> list[str]:
    """`self.foo()` where `foo` is neither defined, assigned, nor inherited."""
    out: list[str] = []
    for cls_node in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        defined = {
            n.name
            for n in ast.walk(cls_node)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assigned = {
            n.attr
            for n in ast.walk(cls_node)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == "self"
            and isinstance(n.ctx, ast.Store)
        }
        assigned |= {
            t.id for n in cls_node.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
        }
        inherited = _class_members(path, cls_node.name)
        for n in ast.walk(cls_node):
            if not (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "self"
            ):
                continue
            name = n.func.attr
            if name not in defined and name not in assigned and name not in inherited:
                out.append(
                    f"{rel}:{n.lineno}: self.{name}() is not defined on "
                    f"{cls_node.name} or its bases"
                )
    return out


def _action_attr_violations(tree: ast.AST, rel: str) -> list[str]:
    """`action.name` and friends — attribute access on a parameter annotated `Action`."""
    allowed, _ = _field_spec(connector_types.Action)
    allowed |= {n for n in dir(connector_types.Action) if not n.startswith("__")}
    out: list[str] = []
    for fn in [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]:
        params = {
            a.arg
            for a in list(fn.args.args) + list(fn.args.kwonlyargs)
            if isinstance(a.annotation, ast.Name) and a.annotation.id == "Action"
        }
        if not params:
            continue
        for n in ast.walk(fn):
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id in params
                and n.attr not in allowed
            ):
                out.append(
                    f"{rel}:{n.lineno}: {n.value.id}.{n.attr} — Action has no such field "
                    f"(did you mean action_type / params?)"
                )
    return out


def _scan() -> list[str]:
    findings: list[str] = []
    for path in _connector_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = _rel(path)
        findings += _model_violations(tree, rel)
        findings += _self_call_violations(tree, path, rel)
        findings += _action_attr_violations(tree, rel)
    return findings


def test_connectors_construct_real_types_and_call_real_methods():
    findings = _scan()
    assert not findings, (
        "Connector code does not match the real connector types — this code cannot have run:\n"
        + "\n".join(f"  {f}" for f in findings)
    )


def test_guard_detects_a_planted_violation(tmp_path):
    """The guard must have teeth: a planted bad construction is caught."""
    bad = tmp_path / "connector.py"
    bad.write_text(
        "from navig.connectors.types import Action, Resource\n"
        "class C:\n"
        "    async def act(self, action: Action):\n"
        "        self._nope()\n"
        "        return Resource(id='x', body='y')\n"
        "    def go(self, action: Action):\n"
        "        return action.name\n",
        encoding="utf-8",
    )
    tree = ast.parse(bad.read_text(encoding="utf-8"))
    found = (
        _model_violations(tree, "planted")
        + _self_call_violations(tree, bad, "planted")
        + _action_attr_violations(tree, "planted")
    )
    joined = "\n".join(found)
    assert "no such field" in joined  # Resource(body=)
    assert "missing required" in joined  # Resource without source/preview
    assert "self._nope() is not defined" in joined
    assert "action.name" in joined
