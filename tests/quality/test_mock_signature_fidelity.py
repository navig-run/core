"""A fake that mirrors the CALL SITE instead of the REAL SIGNATURE certifies the bug.

`unittest.mock.MagicMock` answers to anything. It accepts any keyword, returns another
MagicMock, and supports `__getitem__` — so a test that stands a MagicMock in for a real
class cannot tell a correct call from a broken one. Four shipped defects hid behind
exactly that this month, and in each case the test was not merely silent, it was
*wrong*:

* `tests/mcp/test_mcp_memory_tools.py` asserted
  ``instance.retrieve.assert_called_once_with(query="q", limit=5, token_budget=500)``.
  `FactRetriever.retrieve` takes ``(query, category, max_tokens, config_override)`` —
  neither `limit` nor `token_budget` exists. The MCP tool `memory_retrieve` raised
  TypeError on every call, and the test named `test_passes_limit_and_token_budget`
  guaranteed it kept doing so.
* `tests/commands/test_backup_command_core.py` defined a fake whose
  ``execute_command(self, _cmd)`` took the command alone, mirroring a call that omitted
  the required `server_config`. The test is named ``..._without_type_error``.
* `tests/security/test_security_commands.py` fed back ``{"exit_code": 0, …}`` where
  `execute_command` returns a ``CompletedProcess``, so 33 dict reads across every
  `navig host security` subcommand stayed green while raising in production.
* `tools/web.get_web_config` was patched wholesale, so the test could verify the
  coercion of a value that never arrived.

This guard closes the two halves that can be decided statically.

1. **Assertions.** ``mock.assert_called_with(...)`` for a patched function, and
   ``mock.<method>.assert_called_with(...)`` where `mock` stands in for a resolvable
   navig class: the asserted arguments must bind to the real signature. If they do not,
   the test is pinning a call that raises. (Aliases are followed — `instance =
   MockCls.return_value` is how tests usually spell it, and an earlier version of this
   guard missed its own motivating bug by not resolving them.)
2. **Hand-rolled fakes.** A locally-defined class installed under a real class's name
   must not be *narrower* than the real API: if the fake's method cannot accept a call
   the real signature requires, then the only calls the test can exercise are wrong ones.

What it deliberately does not do: require `autospec=True` everywhere. 234 class patches
in this suite have no spec, and converting them wholesale is a large blind change. The
rule here is narrower and provable — the assertion, or the fake, contradicts the real
signature — so every finding is a real defect rather than a style preference.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]

_ASSERT_CALLS = {
    "assert_called_with",
    "assert_called_once_with",
    "assert_any_call",
    "assert_has_calls",
}


def _resolve(dotted: str) -> object | None:
    """Import ``a.b.C`` and return ``C``, trying progressively shorter module prefixes."""
    parts = dotted.split(".")
    for i in range(len(parts) - 1, 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:i]))
        except Exception:  # noqa: BLE001 — an unimportable module is not our business
            continue
        obj: object = module
        try:
            for attr in parts[i:]:
                obj = getattr(obj, attr)
        except AttributeError:
            return None
        return obj
    return None


def _patched_classes(fn: ast.AST) -> dict[str, str]:
    """``{local_mock_name: dotted_target}`` for patches bound to a name in this function."""
    bound: dict[str, str] = {}

    def is_patch(call: ast.Call) -> bool:
        f = call.func
        return (isinstance(f, ast.Name) and f.id == "patch") or (
            isinstance(f, ast.Attribute) and f.attr == "patch"
        )

    # decorator form — decorators apply bottom-up, so the last one is the first arg
    targets = [
        d.args[0].value
        for d in getattr(fn, "decorator_list", [])
        if isinstance(d, ast.Call)
        and is_patch(d)
        and d.args
        and isinstance(d.args[0], ast.Constant)
        and isinstance(d.args[0].value, str)
    ]
    argnames = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
    for i, target in enumerate(reversed(targets)):
        if i < len(argnames):
            bound[argnames[i]] = target

    # context-manager form — `with patch("a.b.C") as m:`
    for node in ast.walk(fn):
        if isinstance(node, ast.withitem) and isinstance(node.context_expr, ast.Call):
            call = node.context_expr
            if (
                is_patch(call)
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(node.optional_vars, ast.Name)
            ):
                bound[node.optional_vars.id] = call.args[0].value

    # Aliases — `instance = MockRetriever.return_value` is how tests usually spell it,
    # and missing it is how this guard first failed to see its own motivating bug.
    # Repeat to a fixed point so `a = m.return_value; b = a` resolves too.
    for _ in range(3):
        grew = False
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name) or target.id in bound:
                continue
            root = _mock_root(node.value)
            if root and root in bound:
                bound[target.id] = bound[root]
                grew = True
        if not grew:
            break
    return bound


def _mock_root(node: ast.AST) -> str | None:
    """The base mock name of ``m.return_value.method`` / ``m.method``."""
    while isinstance(node, ast.Attribute):
        if node.attr not in ("return_value",):
            node = node.value
            continue
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _asserted_method(func: ast.Attribute) -> tuple[str, str] | None:
    """For ``m.return_value.foo.assert_called_with``, return ``(mock_name, "foo")``."""
    owner = func.value  # the thing the assert_* is called on
    if not isinstance(owner, ast.Attribute):
        return None
    method = owner.attr
    if method == "return_value":
        return None
    root = _mock_root(owner.value)
    return (root, method) if root else None


def _binds(func: object, call: ast.Call) -> str | None:
    """Return a message if the asserted arguments cannot bind to the real signature."""
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return None
    params = list(sig.parameters.values())
    if params and params[0].name in ("self", "cls"):
        sig = sig.replace(parameters=params[1:])
    if any(isinstance(a, ast.Starred) for a in call.args):
        return None
    if any(k.arg is None for k in call.keywords):
        return None
    args = [object()] * len(call.args)
    kwargs = {k.arg: object() for k in call.keywords if k.arg}
    try:
        sig.bind(*args, **kwargs)
    except TypeError as exc:
        return f"{exc} — the real signature is {sig}"
    return None


def _scan_assertions() -> list[str]:
    findings: list[str] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(TESTS.parent).as_posix()
        for fn in [
            n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]:
            bound = _patched_classes(fn)
            if not bound:
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr not in _ASSERT_CALLS:
                    continue

                # (a) assertion directly on the bound mock — the patched target is the
                # callable itself (`patch("navig.x.send")` → `m.assert_called_with(...)`).
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id in bound:
                    target = _resolve(bound[owner.id])
                    if inspect.isfunction(target) or inspect.ismethod(target):
                        if node.func.attr != "assert_has_calls":
                            problem = _binds(target, node)
                            if problem:
                                findings.append(
                                    f"{rel}:{node.lineno}: asserted call to "
                                    f"{bound[owner.id]}() cannot bind: {problem}"
                                )
                    continue

                # (b) assertion on a method of a patched class's instance
                found = _asserted_method(node.func)
                if not found:
                    continue
                mock_name, method = found
                if mock_name not in bound:
                    continue
                cls = _resolve(bound[mock_name])
                if not inspect.isclass(cls):
                    continue
                real = getattr(cls, method, None)
                if real is None or not callable(real):
                    findings.append(
                        f"{rel}:{node.lineno}: asserts a call to {cls.__name__}.{method}(), "
                        f"which does not exist"
                    )
                    continue
                if node.func.attr == "assert_has_calls":
                    continue  # argument shape is a list of call() objects
                problem = _binds(real, node)
                if problem:
                    findings.append(
                        f"{rel}:{node.lineno}: asserted call to {cls.__name__}.{method}() "
                        f"cannot bind: {problem}"
                    )
    return findings


def _installed_fakes(tree: ast.AST) -> dict[str, str]:
    """``{local_class_name: dotted_target}`` for fakes installed under a real name."""
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    installs: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        # monkeypatch.setattr(mod, "C", Fake)
        if fname == "setattr" and len(node.args) >= 3:
            target, attr, value = node.args[0], node.args[1], node.args[2]
            if (
                isinstance(value, ast.Name)
                and value.id in classes
                and isinstance(attr, ast.Constant)
                and isinstance(target, ast.Constant)
            ):
                installs[value.id] = f"{target.value}.{attr.value}"
        # monkeypatch.setattr("a.b.C", Fake)
        if fname == "setattr" and len(node.args) == 2:
            target, value = node.args
            if (
                isinstance(value, ast.Name)
                and value.id in classes
                and isinstance(target, ast.Constant)
                and isinstance(target.value, str)
            ):
                installs[value.id] = target.value
        # monkeypatch.setitem(sys.modules, "a.b", SimpleNamespace(C=Fake)) — how the
        # backup regression actually installed its fake: a whole stand-in module.
        if fname == "setitem" and len(node.args) >= 3:
            module, replacement = node.args[1], node.args[2]
            if (
                isinstance(module, ast.Constant)
                and isinstance(module.value, str)
                and isinstance(replacement, ast.Call)
            ):
                rf = replacement.func
                rname = rf.attr if isinstance(rf, ast.Attribute) else getattr(rf, "id", "")
                if rname == "SimpleNamespace":
                    for kw in replacement.keywords:
                        if kw.arg and isinstance(kw.value, ast.Name) and kw.value.id in classes:
                            installs[kw.value.id] = f"{module.value}.{kw.arg}"
        # patch("a.b.C", Fake)  /  patch("a.b.C", new=Fake)
        if fname == "patch" and node.args and isinstance(node.args[0], ast.Constant):
            new = node.args[1] if len(node.args) > 1 else None
            if new is None:
                new = next((k.value for k in node.keywords if k.arg == "new"), None)
            if isinstance(new, ast.Name) and new.id in classes:
                installs[new.id] = node.args[0].value
    return installs


def _accepts(method: ast.FunctionDef) -> tuple[int, bool]:
    a = method.args
    positional = [x.arg for x in a.posonlyargs + a.args if x.arg not in ("self", "cls")]
    return len(positional), (a.vararg is not None or a.kwarg is not None)


def _requires(func: object) -> int | None:
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return None
    count = 0
    for name, p in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            return None  # accepts anything; nothing to compare
        if p.default is p.empty and p.kind != p.KEYWORD_ONLY:
            count += 1
    return count


def _scan_fakes() -> list[str]:
    findings: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        if not classes:
            continue
        rel = path.relative_to(TESTS.parent).as_posix()
        for fake_name, target in _installed_fakes(tree).items():
            real = _resolve(target)
            if not inspect.isclass(real):
                continue
            for method in [
                m
                for m in classes[fake_name].body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not m.name.startswith("__")
            ]:
                real_method = getattr(real, method.name, None)
                if real_method is None or not callable(real_method):
                    continue
                required = _requires(real_method)
                if required is None:
                    continue
                accepts, flexible = _accepts(method)
                if not flexible and accepts < required:
                    findings.append(
                        f"{rel}:{method.lineno}: fake {fake_name}.{method.name}() accepts "
                        f"{accepts} argument(s), but {real.__name__}.{method.name}() requires "
                        f"{required} — a CORRECT call would raise TypeError against this fake"
                    )
    return findings


def test_no_assertion_pins_a_call_the_real_signature_rejects() -> None:
    findings = _scan_assertions()
    assert not findings, (
        "These tests assert that production code called a mocked class in a way the real "
        "class cannot accept. A MagicMock takes any arguments, so the assertion passes "
        "while the real call raises TypeError — the test pins the bug in place. Read the "
        "real signature and assert the call that actually works:\n"
        + "\n".join(f"  {f}" for f in findings)
    )


def test_no_hand_rolled_fake_is_narrower_than_the_real_api() -> None:
    findings = _scan_fakes()
    assert not findings, (
        "These fakes stand in for a real class but accept fewer arguments than it "
        "requires, so the only calls they permit are the wrong ones. Mirror the real "
        "signature (or use create_autospec):\n" + "\n".join(f"  {f}" for f in findings)
    )


def test_assertion_guard_detects_the_real_regression() -> None:
    """Teeth: the exact assertion that shipped in tests/mcp/test_mcp_memory_tools.py."""
    from navig.memory.fact_retriever import FactRetriever

    # Spelled exactly as the shipped test did — through an `instance` alias. An earlier
    # version of this teeth test asserted on the direct chain instead, so the guard
    # passed here while missing the very bug it exists for.
    src = (
        "def test_x():\n"
        "    with patch('navig.memory.fact_retriever.FactRetriever') as MockRetriever:\n"
        "        instance = MockRetriever.return_value\n"
        "        instance.retrieve.assert_called_once_with(\n"
        "            query='q', limit=5, token_budget=500)\n"
    )
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    bound = _patched_classes(fn)
    assert bound["MockRetriever"] == "navig.memory.fact_retriever.FactRetriever"
    assert bound["instance"] == "navig.memory.fact_retriever.FactRetriever"  # the alias

    call = next(
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "assert_called_once_with"
    )
    assert _asserted_method(call.func) == ("instance", "retrieve")
    assert _binds(FactRetriever.retrieve, call) is not None  # the bug

    good = ast.parse("f(query='q', max_tokens=500)").body[0].value
    assert _binds(FactRetriever.retrieve, good) is None  # the fix


def test_fake_guard_detects_the_real_regression() -> None:
    """Teeth: the narrow fake that shipped in tests/commands/test_backup_command_core.py."""
    from navig.remote import RemoteOperations

    src = (
        "class _Remote:\n"
        "    def execute_command(self, _cmd):\n"
        "        return None\n"
        "def wire(monkeypatch):\n"
        "    monkeypatch.setattr('navig.remote.RemoteOperations', _Remote)\n"
    )
    tree = ast.parse(src)
    assert _installed_fakes(tree) == {"_Remote": "navig.remote.RemoteOperations"}

    method = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "execute_command"
    )
    assert _accepts(method) == (1, False)
    assert _requires(RemoteOperations.execute_command) == 2  # command + server_config
