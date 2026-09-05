"""Calling a method that does not exist is a bug the type checker never sees here.

The bug class
-------------
A module constructs a navig class and then calls a method on it that was never
implemented — usually because the API was remembered rather than read::

    remote = RemoteOperations(host_config)     # takes a ConfigManager, not a host config
    result = remote.execute(command)           # the real name is execute_command()
    return (result.success, result.stdout, …)  # it returns a CompletedProcess

Python resolves attributes at *call* time, so nothing fails until that line runs. And
these call sites are almost always inside a ``try``, so the ``AttributeError`` is caught
and reported as an ordinary failure. The result is code that is dead on arrival while
looking merely unlucky:

  * ``navig chat`` — ``ai.chat(...)`` (the real name is ``ask``, and it is not async):
    every invocation printed "AI chat error: 'AIAssistant' object has no attribute 'chat'";
  * ``PluginAPI.run_remote`` / ``upload_file`` / ``download_file`` — the whole remote
    surface of the **plugin SDK**, each swallowed into a soft ``(False, "…")`` return;
  * ``tools/web.get_web_config`` — ``config_manager.get_global_config_value("web")``, so
    the user's entire ``web:`` config was discarded and the documented
    ``web.fetch.enabled`` / ``web.search.enabled`` kill-switches could never be turned off;
  * the deck admin page — ``MCPManager.list()`` (really ``list_servers``) meant it showed
    zero MCP servers, forever, behind one log line.

Thirteen sites across eight modules, all invisible to ruff and to the test suite.

The same mistake on RETURN values
---------------------------------
The second half is worse, because the object is correct and only the *result* is
misread. ``RemoteOperations.execute_command()`` returns a ``subprocess.CompletedProcess``,
and callers treated it as a mapping::

    result = remote_ops.execute_command("sudo ufw status", server_config)
    if result["exit_code"] != 0:                     # TypeError: not subscriptable
        … result.get("stderr", "Unknown error")      # AttributeError: no attribute 'get'

That was every command in ``commands/security.py`` (33 reads across firewall, fail2ban,
the SSH audit and the security scan), plus ``commands/remote.py``'s package installer.
It survived because the suite patched ``RemoteOperations`` with a plain ``MagicMock`` —
and ``MagicMock()["exit_code"]`` returns another MagicMock quite happily, so the dict
access "worked" in tests and raised in production.

What this guard does
--------------------
For every function it tracks two kinds of local:

1. ``x = SomeClass(...)`` where ``SomeClass`` is imported from ``navig.*`` — each
   ``x.attr`` is checked against the class's MRO ``__dict__``, its annotations, and every
   ``self.attr`` assigned in its source;
2. ``y = x.method(...)`` where that method has a resolvable return annotation — each
   ``y.attr`` is checked the same way, and ``y[...]`` is rejected when the returned type
   has no ``__getitem__``.

Ambiguity is skipped, never guessed: a name bound more than once in the same function
(a loop variable that shares a name with a later assignment, a conditional rebind) tells
us nothing reliable, a class with ``__getattr__`` can synthesise anything, and a return
annotation of ``Any`` or a builtin container says nothing about the surface.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import subprocess
import typing
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
_SKIP_PARTS = {"__pycache__", "builtin", "scaffold-templates"}


def _sources() -> list[Path]:
    return [p for p in sorted(CORE.rglob("*.py")) if not (_SKIP_PARTS & set(p.parts))]


def _navig_imports(tree: ast.AST) -> dict[str, tuple[str, str]]:
    """``{local_name: (module, original_name)}`` for every ``from navig.x import Y``."""
    binds: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if not node.module.startswith("navig"):
                continue
            for alias in node.names:
                if alias.name != "*":
                    binds[alias.asname or alias.name] = (node.module, alias.name)
    return binds


def _bound_names(fn: ast.AST) -> dict[str, int]:
    """How many times each name is bound in this function (any binding form)."""
    counts: dict[str, int] = {}

    def bump(target: ast.AST) -> None:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name):
                counts[sub.id] = counts.get(sub.id, 0) + 1

    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                bump(t)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor, ast.comprehension)):
            bump(node.target)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            bump(node.optional_vars)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            counts[node.name] = counts.get(node.name, 0) + 1
        elif isinstance(node, ast.arg):
            counts[node.arg] = counts.get(node.arg, 0) + 1
    return counts


def _constructed_locals(fn: ast.AST, binds: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Locals unambiguously bound to a navig class instance, once and only once."""
    counts = _bound_names(fn)
    out: dict[str, tuple[str, str]] = {}
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Await):
            value = value.value
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)):
            continue
        if value.func.id not in binds:
            continue
        # Bound anywhere else in this function? Then we cannot say what it holds
        # at the point of use — skip rather than guess (this is what keeps a loop
        # variable that shares a name with a later `t = Table(...)` out of the report).
        if counts.get(target.id, 0) != 1:
            continue
        out[target.id] = binds[value.func.id]
    return out


def _real_attrs(cls: type) -> set[str]:
    """Everything the class actually provides: MRO members, annotations, self.X."""
    names: set[str] = set()
    for klass in cls.__mro__:
        names |= set(vars(klass))
        names |= set(getattr(klass, "__annotations__", {}) or {})
        try:
            src = inspect.getsource(klass)
        except (OSError, TypeError):
            continue
        try:
            tree = ast.parse(src.lstrip())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in ("self", "cls")
            ):
                names.add(node.attr)
    return names


def _resolve(module: str, name: str) -> type | None:
    try:
        obj = getattr(importlib.import_module(module), name)
    except Exception:  # noqa: BLE001 — an unimportable module is not this guard's business
        return None
    if not inspect.isclass(obj):
        return None
    if any("__getattr__" in vars(k) for k in obj.__mro__):
        return None  # can synthesise any attribute; nothing to verify
    return obj


def _returned_locals(
    fn: ast.AST, constructed: dict[str, tuple[str, str]]
) -> dict[str, tuple[type, str]]:
    """Locals bound to the RESULT of a method call whose return type is known.

    This is the second half of the same bug, and the more damaging one:
    ``result = remote_ops.execute_command(...)`` returns a
    ``subprocess.CompletedProcess``, and the caller reads ``result["exit_code"]`` or
    ``result.get("stderr")``. Both raise, and both were invisible to the constructor
    check above — the reads happen on a *return value*, not on the object built here.
    """
    counts = _bound_names(fn)
    out: dict[str, tuple[type, str]] = {}
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or counts.get(target.id, 0) != 1:
            continue
        value = node.value
        if isinstance(value, ast.Await):
            value = value.value
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)):
            continue
        receiver = value.func.value
        if not (isinstance(receiver, ast.Name) and receiver.id in constructed):
            continue
        module, name = constructed[receiver.id]
        cls = _resolve(module, name)
        if cls is None:
            continue
        method = getattr(cls, value.func.attr, None)
        if method is None:
            continue
        try:
            hints = typing.get_type_hints(method)
        except Exception:  # noqa: BLE001 — unresolvable annotations are not our business
            continue
        returned = hints.get("return")
        # Only concrete classes with a fixed surface; builtins and Any tell us nothing.
        if not inspect.isclass(returned) or returned in _UNINFORMATIVE_RETURNS:
            continue
        if any("__getattr__" in vars(k) for k in returned.__mro__):
            continue
        out[target.id] = (returned, f"{name}.{value.func.attr}()")
    return out


# Returns that tell us nothing about the available surface. `typing.Any` is here
# deliberately: since 3.11 it passes `inspect.isclass`, and treating it as a real class
# reports every attribute access on an `-> Any` result as missing.
_UNINFORMATIVE_RETURNS = (
    bool, int, float, str, bytes, dict, list, tuple, set, type(None), object, typing.Any,
)
_MAPPING_METHODS = {"get", "keys", "items", "values"}


def _scan() -> list[str]:
    findings: list[str] = []
    seen: set[str] = set()
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        binds = _navig_imports(tree)
        if not binds:
            continue
        rel = path.relative_to(CORE.parent).as_posix()

        def record(key: str) -> None:
            if key not in seen:
                seen.add(key)
                findings.append(key)

        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            local = _constructed_locals(fn, binds)
            returned = _returned_locals(fn, local) if local else {}
            if not local and not returned:
                continue
            for node in ast.walk(fn):
                # 1. attribute access on a constructed instance
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    if node.value.id in local:
                        module, name = local[node.value.id]
                        cls = _resolve(module, name)
                        if cls is not None and node.attr not in _real_attrs(cls):
                            record(
                                f"{rel}:{node.lineno}: {node.value.id}.{node.attr} — "
                                f"{name} has no such attribute"
                            )
                    # 2. attribute access on a returned value
                    elif node.value.id in returned:
                        cls, label = returned[node.value.id]
                        if node.attr not in _real_attrs(cls):
                            hint = (
                                " (it is not a mapping)"
                                if node.attr in _MAPPING_METHODS
                                else ""
                            )
                            record(
                                f"{rel}:{node.lineno}: {node.value.id}.{node.attr} — "
                                f"{label} returns {cls.__name__}, which has no such attribute{hint}"
                            )
                # 3. subscripting a returned value that is not subscriptable
                elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                    if node.value.id in returned:
                        cls, label = returned[node.value.id]
                        if not hasattr(cls, "__getitem__"):
                            record(
                                f"{rel}:{node.lineno}: {node.value.id}[...] — "
                                f"{label} returns {cls.__name__}, which is not subscriptable"
                            )
                # 4. calling a method with arguments its signature cannot accept
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    receiver = node.func.value
                    if isinstance(receiver, ast.Name) and receiver.id in local:
                        module, name = local[receiver.id]
                        cls = _resolve(module, name)
                        problem = _call_mismatch(cls, node.func.attr, node) if cls else None
                        if problem:
                            record(f"{rel}:{node.lineno}: {receiver.id}.{node.func.attr}() {problem}")
    return findings


def _call_mismatch(cls: type, method: str, call: ast.Call) -> str | None:
    """Describe how this call fails to match the real signature, or None.

    Only decides when it can be certain: a ``*args``/``**kwargs`` signature accepts
    anything, and a splatted call site could supply anything, so both are skipped.
    """
    func = getattr(cls, method, None)
    if func is None or not callable(func):
        return None
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return None
    params = [p for p in sig.parameters.values() if p.name not in ("self", "cls")]
    if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params):
        return None
    if any(isinstance(a, ast.Starred) for a in call.args):
        return None
    if any(k.arg is None for k in call.keywords):
        return None

    given_kw = {k.arg for k in call.keywords}
    required = [
        p.name for p in params if p.default is p.empty and p.kind != p.KEYWORD_ONLY
    ]
    missing = [r for i, r in enumerate(required) if i >= len(call.args) and r not in given_kw]
    if missing:
        return f"is missing required argument(s) {missing} — the real signature is {method}{sig}"

    unknown = sorted(given_kw - {p.name for p in params})
    if unknown:
        return f"passes unknown keyword argument(s) {unknown} — the real signature is {method}{sig}"

    if len(call.args) > len(params):
        return f"passes {len(call.args)} positional args; {method}{sig} takes {len(params)}"
    return None


def test_no_calls_to_methods_that_do_not_exist() -> None:
    findings = _scan()
    assert not findings, (
        "These lines use an attribute the class does not have. Python only raises at the "
        "moment the line runs, and these call sites are usually inside a try/except — so "
        "the code is dead on arrival while reporting an ordinary failure. Read the class "
        "and use its real API:\n" + "\n".join(f"  {f}" for f in findings)
    )


def test_guard_detects_a_planted_violation(tmp_path: Path) -> None:
    """Teeth: the exact shape that shipped in plugins/base.py."""
    src = (
        "from navig.remote import RemoteOperations\n"
        "def go(cfg):\n"
        "    remote = RemoteOperations(cfg)\n"
        "    return remote.execute('ls')\n"
    )
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    local = _constructed_locals(fn, _navig_imports(tree))
    assert local == {"remote": ("navig.remote", "RemoteOperations")}

    cls = _resolve("navig.remote", "RemoteOperations")
    assert cls is not None
    assert "execute" not in _real_attrs(cls)       # the bug
    assert "execute_command" in _real_attrs(cls)   # the real API


def test_guard_skips_ambiguously_bound_names() -> None:
    """A name bound twice says nothing reliable — skipping beats a false report.

    This is the real shape from ``commands/skills.py``: ``t`` is a comprehension
    variable over strings, and a ``t = Table(...)`` appears later in the same function.
    """
    src = (
        "from navig.console_helper import Table\n"
        "def render(terms, body):\n"
        "    present = [t for t in terms if t.lower() in body]\n"
        "    t = Table()\n"
        "    t.add_row(str(len(present)))\n"
        "    return t\n"
    )
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    assert _constructed_locals(fn, _navig_imports(tree)) == {}


def test_guard_detects_a_result_used_as_a_dict() -> None:
    """Teeth for the second shape: the 33 sites that shipped in commands/security.py."""
    src = (
        "from navig.remote import RemoteOperations\n"
        "def go(cm, cfg):\n"
        "    ops = RemoteOperations(cm)\n"
        "    result = ops.execute_command('ufw status', cfg)\n"
        "    if result['exit_code'] != 0:\n"
        "        return result.get('stderr', 'Unknown error')\n"
        "    return result['stdout']\n"
    )
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    constructed = _constructed_locals(fn, _navig_imports(tree))
    returned = _returned_locals(fn, constructed)

    assert "result" in returned
    cls, label = returned["result"]
    assert cls is subprocess.CompletedProcess
    assert label == "RemoteOperations.execute_command()"
    assert not hasattr(cls, "__getitem__")   # the subscripts raise TypeError
    assert "get" not in _real_attrs(cls)     # .get() raises AttributeError
    assert "returncode" in _real_attrs(cls)  # the real API


def test_guard_does_not_flag_correct_use_of_a_returned_value() -> None:
    src = (
        "from navig.remote import RemoteOperations\n"
        "def go(cm, cfg):\n"
        "    ops = RemoteOperations(cm)\n"
        "    result = ops.execute_command('uptime', cfg)\n"
        "    return result.returncode, result.stdout, result.stderr\n"
    )
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    returned = _returned_locals(fn, _constructed_locals(fn, _navig_imports(tree)))
    cls, _ = returned["result"]
    for attr in ("returncode", "stdout", "stderr"):
        assert attr in _real_attrs(cls)


def test_instance_attributes_assigned_in_init_are_recognised() -> None:
    """``self.x = …`` counts as provided — otherwise the guard would be all noise."""
    cls = _resolve("navig.remote", "RemoteOperations")
    assert cls is not None
    assert "config" in _real_attrs(cls)  # set as self.config in __init__
