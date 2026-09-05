"""The JSON-store-wipe read-modify-write anti-pattern must never come back.

This is the JSON twin of ``test_no_config_wipe_pattern`` (which guards the YAML
``safe_load_yaml(...) or {}`` shape). A per-feature ``.json`` store that does::

    data = {}
    try:
        data = json.loads(path.read_text())   # a FILE read
    except Exception:
        data = {}                             # swallow → default
    data.update(...)
    path.write_text(json.dumps(data))         # write the SAME var back to the FILE

silently DELETES every record on a transient OS lock or a half-written read: the
read collapses to ``{}`` and the write then persists that emptiness over the store.
This wiped `cron_jobs.json`, the email rules, spaces registry, sidecar metadata, the
skills-lock, VS Code / MCP editor configs — the whole config-wipe class, but for JSON.
The YAML guard is BLIND to it (it watches ``safe_load_yaml``/``atomic_write_yaml``,
never ``json.load``/``json.dump``).

The fix everywhere is ``navig.core.json_io``: ``load_json_for_update`` REFUSES an
unreadable-but-populated file (raises ``JsonReadError`` so the caller aborts the save)
and quarantines a corrupt one, and ``atomic_write_json`` replaces the hand-rolled write.

Precision matters — a file-level heuristic ("swallows a json.load and writes json")
flags ~149 files, almost all noise (caches, read-config-emit-output, display commands).
So this guard is tight and data-flow aware. It flags a variable ONLY when, in the SAME
function scope:
  1. it is assigned from a FILE json-read — ``json.load(handle)`` or
     ``json.loads(<...>.read_text()/.read())`` — inside a ``try``, AND
  2. a BROAD ``except`` for that try reassigns it to a ``{}``/``[]`` default, AND
  3. that same variable is later serialised back TO A FILE — ``json.dump(v, f)`` (>=2
     positional args) or ``<path>.write_text(json.dumps(v...))`` / ``.write(...)``.

That deliberately excludes the two big false-positive families the loose heuristic hits:
a config-STRING read (``json.loads(raw)`` where ``raw`` isn't a file read) and a
display dump (``console.print_json(json.dumps(x))`` — not a file write).
"""

from __future__ import annotations

import ast
from pathlib import Path

# Pure static analysis — locate the sources by path, never import them (fast, usable as a
# pre-push gate, independent of whether the tree imports cleanly).
# core/tests/config/<this> -> parents[2]==core, [3]==repo.
_CORE_ROOT = Path(__file__).resolve().parents[2] / "navig"
_PLUGINS_ROOT = Path(__file__).resolve().parents[3] / "plugins"

_EXCLUDE_DIRS = {"tests", "test", ".venv", "node_modules", ".dev", "build", "dist", "__pycache__"}

# A handler is "broad" (swallows the read failure) if it's a bare ``except:`` or names one
# of these — exactly the exception types a transient lock / a corrupt file raises here.
_BROAD_EXCEPTS = {
    "Exception", "BaseException", "OSError", "IOError", "PermissionError",
    "JSONDecodeError", "ValueError", "TypeError",
}


def _scan_roots() -> list[Path]:
    """core/navig, plus plugins/ when present — core also ships standalone, without it."""
    roots = [_CORE_ROOT]
    if _PLUGINS_ROOT.is_dir():
        roots.append(_PLUGINS_ROOT)
    return roots


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _call_name(node: ast.AST) -> str | None:
    return _name(node.func) if isinstance(node, ast.Call) else None


def _is_json_call(call: ast.Call, names: set[str]) -> bool:
    """A call to json.<name>(...) or a bare <name>(...) (from json import <name>)."""
    if _call_name(call) not in names:
        return False
    func = call.func
    return (isinstance(func, ast.Attribute) and _name(func.value) == "json") or isinstance(func, ast.Name)


def _is_file_json_load(value: ast.AST) -> bool:
    """``json.load(handle)`` OR ``json.loads(<...>.read_text()/.read())`` — a read from a FILE.

    A bare ``json.loads(raw)`` over a string/config value is deliberately NOT matched: that
    is a parse, not a store read, and rewriting a config value never wipes a file.
    """
    if not (isinstance(value, ast.Call) and _is_json_call(value, {"load", "loads"})):
        return False
    if _call_name(value) == "load":
        return True  # json.load(fp) always consumes a file object
    if not value.args:
        return False
    arg = value.args[0]
    return isinstance(arg, ast.Call) and _call_name(arg) in {"read_text", "read"}


def _is_default_literal(value: ast.AST) -> bool:
    """`{}` / `[]` / `dict()` / `list()` — the empty default a swallowed read falls back to."""
    if isinstance(value, (ast.Dict, ast.List)):
        return True
    return isinstance(value, ast.Call) and _name(value.func) in {"dict", "list"}


def _handler_is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    parts = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(_name(p) in _BROAD_EXCEPTS for p in parts)


def _own_nodes(fn: ast.AST):
    """Yield nodes in fn's body, NOT descending into nested function/lambda scopes.

    Each nested function is visited on its own (ast.walk finds it separately), so a
    read-only load in one closure is never conflated with a write in a sibling closure.
    """

    def walk(node: ast.AST):
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield from walk(child)

    for stmt in getattr(fn, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield from walk(stmt)


def _assigns_matching(stmt: ast.AST, pred) -> set[str]:
    """Names bound by ``name = <value for which pred(value) is True>``."""
    out: set[str] = set()
    if isinstance(stmt, ast.Assign) and pred(stmt.value):
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                out.add(target.id)
    return out


def _tainted_names(fn: ast.AST) -> dict[str, int]:
    """Names read from a FILE json-load in a try whose broad except reassigns them to a
    default — i.e. the value silently becomes ``{}``/``[]`` when the read fails."""
    tainted: dict[str, int] = {}
    for node in _own_nodes(fn):
        if not isinstance(node, ast.Try):
            continue
        loaded: set[str] = set()
        for sub in ast.walk(node):  # the try body (handlers included) — load site lives in body
            loaded |= _assigns_matching(sub, _is_file_json_load)
        if not loaded:
            continue
        for handler in node.handlers:
            if not _handler_is_broad(handler):
                continue
            for sub in handler.body:
                for nm in _assigns_matching(sub, _is_default_literal):
                    if nm in loaded:
                        tainted.setdefault(nm, node.lineno)
    return tainted


def _writes_var_to_file(fn: ast.AST, name: str) -> int | None:
    """Lineno at which ``name`` is serialised back TO A FILE, else None.

    Two shapes: ``json.dump(name, <file>)`` (>=2 positional args → writes a file object),
    or ``<path>.write_text(...)`` / ``.write(...)`` whose argument subtree serialises
    ``json.dumps(name, ...)``. A ``console.print_json(json.dumps(name))`` display dump is
    NOT a file write, so it is never matched.
    """
    for node in _own_nodes(fn):
        if not isinstance(node, ast.Call):
            continue
        cn = _call_name(node)
        if cn == "dump" and _is_json_call(node, {"dump"}) and len(node.args) >= 2:
            if isinstance(node.args[0], ast.Name) and node.args[0].id == name:
                return node.lineno
        if cn in {"write_text", "write"}:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and _is_json_call(sub, {"dumps"}) and sub.args:
                    first = sub.args[0]
                    if isinstance(first, ast.Name) and first.id == name:
                        return node.lineno
    return None


def _find_violations(tree: ast.AST, rel: str) -> list[str]:
    out: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tainted = _tainted_names(fn)
        for nm, load_ln in tainted.items():
            write_ln = _writes_var_to_file(fn, nm)
            if write_ln is not None:
                out.append(
                    f"{rel}:{write_ln} — {fn.name}() reads '{nm}' from a file with "
                    f"json.load(...) + `except: {nm} = {{}}` (try at line {load_ln}) then writes "
                    f"it back to the file. Use navig.core.json_io.load_json_for_update "
                    f"(raises JsonReadError on a locked/half-written file) + atomic_write_json."
                )
    return out


def _scan(root: Path) -> list[str]:
    violations: list[str] = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root)
        if set(rel.parts) & _EXCLUDE_DIRS:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        violations += _find_violations(tree, f"{root.name}/{rel.as_posix()}")
    return violations


# ── the guard ────────────────────────────────────────────────────────────────


def test_no_json_store_wipe_read_modify_write_survives_in_the_repo():
    """No function in core OR any plugin may read a JSON store from a file, default it to
    ``{}`` on a swallowed failure, and write that same dict back — that silently wipes the
    store on a transient read lock. Route the read through json_io.load_json_for_update."""
    violations: list[str] = []
    for root in _scan_roots():
        violations += _scan(root)
    assert not violations, (
        "JSON-store-wipe read-modify-write pattern reintroduced — route the read through "
        "navig.core.json_io.load_json_for_update (it refuses an unreadable-but-populated "
        "file instead of returning {}) and write with atomic_write_json:\n  "
        + "\n  ".join(violations)
    )


# ── the guard must actually be able to see the pattern ───────────────────────
#
# A guard that can no longer detect anything is worse than none: it reports green over an
# unchecked tree. These pin the detector's teeth so a refactor can't silently blunt it.


def test_the_scan_covers_plugins_not_just_core():
    roots = _scan_roots()
    assert _CORE_ROOT in roots
    if _PLUGINS_ROOT.is_dir():
        assert _PLUGINS_ROOT in roots, "plugins/ present but not scanned — green over an unchecked tree"


def test_guard_flags_the_file_read_default_write_shape():
    """The canonical wipe: file read → except-default → write the same var back to the file."""
    src = (
        "def save(path):\n"
        "    try:\n"
        "        data = json.loads(path.read_text())\n"
        "    except Exception:\n"
        "        data = {}\n"
        "    data['x'] = 1\n"
        "    path.write_text(json.dumps(data, indent=2))\n"
    )
    assert len(_find_violations(ast.parse(src), "synthetic.py")) == 1


def test_guard_flags_the_json_load_handle_and_json_dump_form():
    """`json.load(f)` + `json.dump(data, f)` (the file-object form) is the same class."""
    src = (
        "def save(path):\n"
        "    data = {}\n"
        "    try:\n"
        "        with open(path) as f:\n"
        "            data = json.load(f)\n"
        "    except OSError:\n"
        "        data = {}\n"
        "    data['x'] = 1\n"
        "    with open(path, 'w') as f:\n"
        "        json.dump(data, f)\n"
    )
    assert len(_find_violations(ast.parse(src), "synthetic.py")) == 1


def test_guard_ignores_a_config_string_read():
    """`json.loads(raw)` over a config STRING (not a file read) then dumped is NOT a store
    wipe — this is the `navig mini list` shape and must never be flagged."""
    src = (
        "def show():\n"
        "    raw = _navig_get('mini.agents', '[]')\n"
        "    try:\n"
        "        agents = json.loads(raw)\n"
        "    except Exception:\n"
        "        agents = []\n"
        "    console.print_json(json.dumps(agents))\n"
    )
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_guard_ignores_a_console_display_dump():
    """Even with a real file read, dumping to the console (not a file) is not a write-back."""
    src = (
        "def show(path):\n"
        "    try:\n"
        "        data = json.loads(path.read_text())\n"
        "    except Exception:\n"
        "        data = {}\n"
        "    console.print_json(json.dumps(data))\n"
    )
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_guard_accepts_the_correct_json_io_form():
    src = (
        "def save(path):\n"
        "    data = load_json_for_update(path, default={})\n"
        "    data['x'] = 1\n"
        "    atomic_write_json(data, path)\n"
    )
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_guard_does_not_conflate_two_sibling_closures():
    """A read-only closure and a writer closure that both use `data` must NOT combine into
    a false positive — the scoping bug that would make the guard cry wolf."""
    src = (
        "def outer():\n"
        "    def show(path):\n"
        "        try:\n"
        "            data = json.loads(path.read_text())\n"
        "        except Exception:\n"
        "            data = {}\n"
        "        return data.get('x')\n"
        "    def persist(path):\n"
        "        data = load_json_for_update(path, default={})\n"
        "        atomic_write_json(data, path)\n"
    )
    assert _find_violations(ast.parse(src), "synthetic.py") == []
