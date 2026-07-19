"""Tripwire: no import-time-frozen environment-sensitive path may enter ``navig/``.

PR #179 proved what this pattern costs: a module-level constant derived from
``config_dir()`` (``_LEGACY_DB``) froze the operator's REAL home before test
isolation applied, and vault auto-migration copied the operator's ACTUAL
credentials into isolated test vaults. PR #189 then classified every instance
tree-wide and fixed all 30 real-risk hits across 27 modules with the same
call-time-resolver idiom. This test makes instance #31 unwriteable: it fails
the moment anyone reintroduces the shape.

The shape being banned — a path expression that EXECUTES at import/class-body
time — in any of three positions:

1. module-level ``Assign`` / ``AnnAssign`` whose value contains a call to an
   environment-sensitive root resolver (``config_dir()``, ``data_dir()``,
   ``vault_dir()``, ``Path.home()``, ``expanduser()``, and the first-party
   wrappers around them), however deeply nested (``/`` BinOps, ``str(...)``,
   ``Path(...)``, f-strings, tuples);
2. class-level attribute defaults with the same property — including dataclass
   fields assigned a value directly (``x: Path = config_dir() / ...``) and
   eager ``field(default=...)``;
3. function/method signature defaults (``def f(p=config_dir() / ...)``) —
   evaluated once, at ``def`` time.

Explicitly NOT flagged (the idioms #189 rewrote everything into):

- calls inside function/method BODIES — that is the fix, not the bug;
- ``field(default_factory=lambda: ...)`` / ``default_factory=_resolver`` —
  lambda bodies evaluate at instantiation time;
- ``None`` sentinels (``_SEAM: Path | None = None``) resolved inside functions;
- ``if TYPE_CHECKING:`` blocks — never executed at runtime;
- strings, comments, docstrings — this is an AST check on purpose: a text scan
  would flag the very docstrings that explain the fix (see
  tests/platform/test_no_hardcoded_home.py for the precedent).

Detection is by exact call name. The forbidden set is the environment-sensitive
root resolvers: anything whose result depends on ``NAVIG_CONFIG_DIR`` /
``NAVIG_DATA_DIR`` / the real home. Wrapper resolvers (``memory_dir()``,
``log_dir()``, ...) are included because freezing a wrapper freezes the root
exactly the same way — ``KEY_FACTS_DB_PATH`` (the #141/#143 bug) froze via
``memory_dir()``, not ``config_dir()`` directly.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

NAVIG_PKG = Path(__file__).resolve().parents[2] / "navig"

# ---------------------------------------------------------------------------
# The forbidden calls — environment-sensitive root resolvers.
# ---------------------------------------------------------------------------

#: Matched as a bare name call (``config_dir()``) OR an attribute call
#: (``paths.config_dir()``, ``os.path.expanduser()``) — imports get aliased.
FROZEN_ROOT_CALLS = frozenset(
    {
        # the #179/#189 core set
        "config_dir",
        "data_dir",
        "vault_dir",
        "expanduser",
        # navig.platform.paths wrappers — each one is config_dir()/data_dir()/
        # home under the hood, so freezing it freezes the root identically
        "home_dir",
        "log_dir",
        "cache_dir",
        "workspace_dir",
        "store_dir",
        "blackbox_dir",
        "temp_dir",
        "media_dir",
        "builtin_store_dir",
        "packages_dir",
        "ssh_key_dir",
        "stack_dir",
        "audio_configs_dir",
        "debug_log_path",
        "global_config_path",
        "msg_trace_path",
        "genesis_json_path",
        "entity_json_path",
        "onboarding_json_path",
        "media_budget_path",
        # navig.memory.paths wrappers (the KEY_FACTS_DB_PATH route, #141)
        "memory_dir",
        "navig_home",
    }
)


class Finding(NamedTuple):
    line: int
    target: str  # the constant / parameter being bound
    call: str  # which forbidden call froze it
    where: str  # "module binding" / "class binding" / "signature default"


def _forbidden_call_label(call: ast.Call) -> str | None:
    """Name of the frozen-root call, or None if this call is benign."""
    func = call.func
    if isinstance(func, ast.Name) and func.id in FROZEN_ROOT_CALLS:
        return func.id
    if isinstance(func, ast.Attribute):
        if func.attr in FROZEN_ROOT_CALLS:
            return func.attr
        # ``<anything>.home()`` with no args ≈ Path.home(). Deliberately NOT
        # pinned to the receiver name ``Path`` — main.py imports it as _Path
        # (see tests/platform/test_no_hardcoded_home.py for the precedent).
        if func.attr == "home" and not call.args and not call.keywords:
            return "home"
    return None


def _frozen_calls_in(expr: ast.AST) -> list[tuple[int, str]]:
    """All frozen-root calls that EXECUTE when *expr* is evaluated.

    Descends through arbitrary nesting (``/`` BinOps, ``str()``/``Path()``
    wrappers, f-strings, tuples, comprehensions) but NOT into lambda bodies:
    a lambda body runs at call time, which is precisely the allowed
    ``default_factory`` idiom. Lambda *parameter defaults* still evaluate at
    binding time, so those are walked.
    """
    hits: list[tuple[int, str]] = []

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.Lambda):
            for default in [*node.args.defaults, *filter(None, node.args.kw_defaults)]:
                walk(default)
            return  # the body is call-time — the fix idiom, not the bug
        if isinstance(node, ast.Call):
            label = _forbidden_call_label(node)
            if label is not None:
                hits.append((getattr(node, "lineno", 0), label))
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(expr)
    return hits


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _binding_name(stmt: ast.Assign | ast.AnnAssign | ast.AugAssign) -> str:
    if isinstance(stmt, ast.Assign):
        return ", ".join(ast.unparse(t) for t in stmt.targets)
    return ast.unparse(stmt.target)


def scan_module(source: str, filename: str = "<snippet>") -> list[Finding]:
    """All frozen-path bindings in *source* (module + class scope + defaults)."""
    findings: list[Finding] = []
    tree = ast.parse(source, filename=filename)
    _try_star = getattr(ast, "TryStar", ast.Try)

    def visit(stmts: list[ast.stmt], scope: str) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                if stmt.value is None:  # bare annotation — no value to freeze
                    continue
                name = _binding_name(stmt)
                for line, call in _frozen_calls_in(stmt.value):
                    findings.append(Finding(line, name, call, f"{scope} binding"))
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = stmt.args
                defaults = [*args.defaults, *filter(None, args.kw_defaults)]
                for default in defaults:
                    for line, call in _frozen_calls_in(default):
                        findings.append(Finding(line, f"{stmt.name}()", call, "signature default"))
                # bodies are call-time — never descended into
            elif isinstance(stmt, ast.ClassDef):
                visit(stmt.body, "class")
            elif isinstance(stmt, ast.If):
                if _is_type_checking(stmt.test):
                    visit(stmt.orelse, scope)  # the else branch IS runtime
                else:
                    visit(stmt.body, scope)
                    visit(stmt.orelse, scope)
            elif isinstance(stmt, (ast.Try, _try_star)):
                visit(stmt.body, scope)
                for handler in stmt.handlers:
                    visit(handler.body, scope)
                visit(stmt.orelse, scope)
                visit(stmt.finalbody, scope)
            elif isinstance(stmt, (ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
                visit(stmt.body, scope)
                visit(getattr(stmt, "orelse", []) or [], scope)
            elif isinstance(stmt, ast.Match):
                for case in stmt.cases:
                    visit(case.body, scope)

    visit(tree.body, "module")
    return findings


# ---------------------------------------------------------------------------
# The allowlist — every entry carries its justification.
# ---------------------------------------------------------------------------


class Allow(NamedTuple):
    #: Binding names allowed in the file, or None to allow the WHOLE file.
    names: frozenset[str] | None
    reason: str


ALLOWED: dict[str, Allow] = {
    "memory/paths.py": Allow(
        names=frozenset({"KEY_FACTS_DB_PATH"}),
        reason=(
            "deprecated back-compat alias, frozen BY DESIGN so legacy callers "
            "keep their old behaviour; its own AST guard "
            "(tests/memory/test_frozen_path_constant_guard.py) bans any "
            "production module from referencing it — new code calls "
            "get_key_facts_db_path()"
        ),
    ),
    "desktop/tray_app.py": Allow(
        names=None,  # whole file
        reason=(
            "spawn-only entry point: the tray runs as its own dedicated "
            "process (`navig tray start` spawns pythonw tray_app.pyw), where "
            "the environment is final before Python even starts; nothing "
            "imports this module into a process whose isolation is configured "
            "after import. Documented benign in #189 — its import-time "
            "mkdir+FileHandler remains that PR's follow-up."
        ),
    ),
}


def _iter_sources() -> list[tuple[str, str]]:
    """(relative-posix-path, source) for every scannable module under navig/."""
    out: list[tuple[str, str]] = []
    for py in sorted(NAVIG_PKG.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            source = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover
            continue
        out.append((py.relative_to(NAVIG_PKG).as_posix(), source))
    return out


def _offenders() -> dict[str, list[Finding]]:
    """Every non-allowlisted frozen-path binding in the real tree."""
    offenders: dict[str, list[Finding]] = {}
    for rel, source in _iter_sources():
        try:
            findings = scan_module(source, filename=rel)
        except SyntaxError:  # pragma: no cover — not this guard's job
            continue
        allow = ALLOWED.get(rel)
        if allow is not None and allow.names is None:
            continue  # whole-file allow
        if allow is not None:
            findings = [f for f in findings if f.target not in allow.names]
        if findings:
            offenders[rel] = findings
    return offenders


def _allowlist_matches() -> set[str]:
    """Allowlist entries that still match at least one finding."""
    matched: set[str] = set()
    by_rel = {rel: src for rel, src in _iter_sources()}
    for rel, allow in ALLOWED.items():
        source = by_rel.get(rel)
        if source is None:
            continue
        findings = scan_module(source, filename=rel)
        if allow.names is None:
            if findings:
                matched.add(rel)
        elif any(f.target in allow.names for f in findings):
            matched.add(rel)
    return matched


# ---------------------------------------------------------------------------
# Guard 0 — the scan must actually scan something.
# ---------------------------------------------------------------------------


def test_scan_root_is_real() -> None:
    """A guard that scans nothing always passes — fail loudly if the root moves."""
    assert NAVIG_PKG.is_dir(), f"expected the navig package at {NAVIG_PKG}"
    assert len(_iter_sources()) > 500, "scanned suspiciously few python files"


# ---------------------------------------------------------------------------
# Self-tests: the checker itself, on source snippets (never real files).
# Positive = the tripwire MUST fire; negative = the allowed idiom MUST pass.
# ---------------------------------------------------------------------------

POSITIVE_SNIPPETS: dict[str, str] = {
    "module assign": 'X = config_dir() / "sessions"\n',
    "module ann-assign": 'X: Path = data_dir() / "db.sqlite"\n',
    "Path.home attr": 'HOME = Path.home() / ".ssh"\n',
    "aliased receiver home": "P = _Path.home()\n",
    "os.path.expanduser": 'RAW = os.path.join(os.path.expanduser("~"), ".navig")\n',
    "bare expanduser": 'RAW = expanduser("~")\n',
    "nested in str()": 'S = str(config_dir() / "x")\n',
    "nested in Path()": 'P = Path(vault_dir(), "keys")\n',
    "f-string": 'F = f"{config_dir()}/cache"\n',
    "tuple assign": 'A, B = config_dir() / "a", config_dir() / "b"\n',
    "wrapper resolver": 'KF = memory_dir() / "key_facts.db"\n',
    "module-level if": 'import sys\nif sys.platform == "win32":\n    X = config_dir() / "x"\n',
    "module-level try": "try:\n    X = config_dir()\nexcept OSError:\n    X = None\n",
    "TYPE_CHECKING else branch": (
        "if TYPE_CHECKING:\n    pass\nelse:\n    X = config_dir() / 'x'\n"
    ),
    "class attr": 'class C:\n    ROOT = data_dir() / "x"\n',
    "dataclass direct default": ("@dataclass\nclass C:\n    root: Path = config_dir() / 'c'\n"),
    "dataclass eager field default": (
        "@dataclass\nclass C:\n    root: Path = field(default=config_dir() / 'c')\n"
    ),
    "function default": 'def f(p=config_dir() / "x"):\n    return p\n',
    "kw-only default": 'def f(*, p=Path.home() / "x"):\n    return p\n',
    "async def default": "async def f(p=data_dir()):\n    return p\n",
    "method default": "class C:\n    def m(self, p=data_dir()):\n        return p\n",
    "lambda parameter default": 'g = lambda p=config_dir() / "x": p\n',
}

NEGATIVE_SNIPPETS: dict[str, str] = {
    "call-time resolver (THE fix idiom)": (
        'def _x_path() -> Path:\n    return config_dir() / "x"\n'
    ),
    "call in method body": ('class C:\n    def m(self):\n        return vault_dir() / "keys"\n'),
    "default_factory lambda": (
        "@dataclass\nclass C:\n    root: Path = field(default_factory=lambda: config_dir() / 'c')\n"
    ),
    "default_factory named resolver": (
        "@dataclass\nclass C:\n    root: Path = field(default_factory=_x_path)\n"
    ),
    "None sentinel seam": "_PROMOTED_FLAG: Path | None = None\n",
    "bare annotation": "X: Path\n",
    "TYPE_CHECKING block": ("if TYPE_CHECKING:\n    X = config_dir() / 'x'\n"),
    "docstring prose": '"""Resolved via config_dir() / "x" at call time."""\n',
    "string constant": 'X = "config_dir()"\n',
    "similarly named call": "X = get_config_dir_name()\n",
    "home with args": 'X = router.home("page")\n',
    "unrelated env read": 'import os\nX = os.environ.get("NAVIG_HOME")\n',
}


@pytest.mark.parametrize("snippet", POSITIVE_SNIPPETS.values(), ids=POSITIVE_SNIPPETS)
def test_tripwire_fires_on_forbidden_shape(snippet: str) -> None:
    assert scan_module(snippet), f"tripwire failed to fire on:\n{snippet}"


@pytest.mark.parametrize("snippet", NEGATIVE_SNIPPETS.values(), ids=NEGATIVE_SNIPPETS)
def test_tripwire_ignores_allowed_idiom(snippet: str) -> None:
    findings = scan_module(snippet)
    assert not findings, f"tripwire false-positive {findings} on:\n{snippet}"


def test_tripwire_names_the_binding_and_line() -> None:
    """The failure output must be actionable: file/line/constant, not a boolean."""
    (finding,) = scan_module('FROZEN = config_dir() / "sessions"\n')
    assert finding.target == "FROZEN"
    assert finding.call == "config_dir"
    assert finding.line == 1
    assert finding.where == "module binding"


# ---------------------------------------------------------------------------
# The tripwire itself — the real tree must stay clean.
# ---------------------------------------------------------------------------


def test_no_frozen_path_constants_in_navig() -> None:
    offenders = _offenders()
    assert not offenders, (
        "Import-time-frozen environment-sensitive path(s) detected — the exact "
        "pattern behind incident #179, where a module-level constant froze the "
        "operator's REAL home before test isolation applied and vault migration "
        "copied their ACTUAL credentials into isolated vaults. A module/class/"
        "default-level `config_dir()/...` (or any wrapper of it) resolves at "
        "IMPORT time, before NAVIG_CONFIG_DIR / NAVIG_DATA_DIR isolation is set "
        "by tests or daemon startup.\n\n"
        "Fix with the call-time-resolver idiom (PR #189 rewrote 30 of these):\n"
        "    def _x_path() -> Path:\n"
        '        return config_dir() / "x"     # resolves at CALL time\n'
        "  - dataclass fields:  field(default_factory=_x_path)\n"
        "  - keyword defaults:  p: Path | None = None, resolved inside the body\n"
        "  - monkeypatch seams: X: Path | None = None, consulted by the resolver\n"
        "If a binding is GENUINELY benign (spawn-only entry point, deprecated "
        "alias with its own guard), add it to ALLOWED in this test with the "
        "justification.\n\n"
        + "\n".join(
            f"  navig/{rel}:{f.line}: {f.target} — {f.call}() in {f.where}"
            for rel, findings in sorted(offenders.items())
            for f in findings
        )
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Fixed one? Delete its entry — the list must never rot into a rubber stamp."""
    stale = sorted(set(ALLOWED) - _allowlist_matches())
    assert not stale, (
        "These ALLOWED entries no longer match any finding — delete them:\n"
        + "\n".join(f"  {rel}: {ALLOWED[rel].reason}" for rel in stale)
    )
