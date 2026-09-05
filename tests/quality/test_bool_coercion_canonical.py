"""Config-boolean coercion must not fork into a fourth truth table.

`navig config set <k> <v>` stores the CLI argument verbatim as a string — so
``config set x false`` writes the string ``"false"`` and ``bool("false")`` is ``True``.
Because of that footgun, ~14 near-identical ``_coerce_bool`` / ``_truthy`` / ``_as_bool``
helpers grew up across the tree with **three incompatible truth tables**
(whitelist-truthy · blacklist-falsy · tri-state), so ``config set x on`` could resolve
differently depending on which subsystem read ``x``. That drift caused a real bug: the
monitor toggle (`notify.py._truthy` + `server.py._monitor_enabled_truthy`) was
case-sensitive and did not know ``on``/``off``, so ``config set monitors.x.enabled ON``
silently read as OFF.

The consolidation onto ``navig.core.coerce.coerce_bool`` fixed that. This guard is the
lock on the door: any helper with an idiomatic bool-coercion **name**
(``_coerce_bool`` / ``_truthy`` / ``_as_bool`` / ``_env_bool`` / ``_bool``) must either
**delegate to** ``coerce_bool`` or sit in the explicit ALLOWLIST of deliberately-independent
readers (MCP tool args; a JSON/entitlement whitelist; deck HTTP query readers). A new
ad-hoc ``_coerce_bool``/``_truthy`` that reintroduces the drift fails the build.

COMPANION GUARD: `test_config_booleans_are_coerced.py` guards the other half — the *read
sites* (a documented `navig config set` toggle, and any negative-polarity security control,
must be read through `coerce_bool`). This file guards helper *definitions*. Neither subsumes
the other: a read can be raw while every helper delegates, and a helper can fork a fourth
truth table while every read is wrapped. Keep both wired.

Scope is deliberately NAME-based, not semantic. Every one of the ~14 historical helpers was
reached for by one of these five names, so the name net catches the real reintroduction
vector precisely. A *semantic* net (flag any ``x in ("true", "false", …)`` membership) was
tried and rejected: it drowns in false positives — every yes/no confirm prompt and every
``request.query.get(...) in ("1", "true")`` HTTP reader is that shape but is NOT a config
coercer, and the AST cannot tell human input from a config string. Precision over reach.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Pure static analysis — locate sources by path, never import them (fast, and usable as a
# pre-push gate even when the tree does not import cleanly). core/tests/quality/<this> ->
# parents[2] == core, parents[3] == repo root.
_CORE_ROOT = Path(__file__).resolve().parents[2] / "navig"
_PLUGINS_ROOT = Path(__file__).resolve().parents[3] / "plugins"

# Not shipped runtime source: tests hold the pattern as fixtures; the rest is build output.
_EXCLUDE_DIRS = {"tests", "test", ".venv", "node_modules", ".dev", "build", "dist", "__pycache__"}

# The canonical module itself defines coerce_bool — never flag its definition.
_CANONICAL_REL = "navig/core/coerce.py"

# Idiomatic bool-helper names — reached for by reflex when reintroducing the pattern.
_HELPER_NAMES = frozenset(
    {"_coerce_bool", "_truthy", "_as_bool", "_to_bool", "_env_bool", "_bool"}
)
# ``_to_bool`` was missing from this net, and that gap is exactly how a helper drifts
# unreviewed: ``core/continuation._to_bool`` grew its own whitelist, never appeared in a
# report, and so was never either delegated or allowlisted. It returned *default* for
# anything that was not a bool or str, so a numeric flag was discarded — and because
# ``merge_policy(**updates)`` defaults each field to its CURRENT value, the caller's
# update was dropped rather than merely mis-read. The name net only works if it covers
# the names people actually reach for.

# Deliberately-independent readers: each has a DIFFERENT contract than config coercion and
# must NOT be blind-swapped onto coerce_bool. Keyed by "<root.name>/<relpath>" -> {func, …}.
# Every entry is proven to still exist by test_allowlist_has_no_stale_entries.
_ALLOWLIST: dict[str, set[str]] = {
    # Blacklist-falsy, unknown→True ("on unless explicitly off") for model_routing.enabled.
    # NOTE: model_routing is NOT WIRED — nothing in core/navig calls it, so no config
    # value reaches this helper. The rationale above also contradicts its own call site,
    # which passes default=False (absent key → OFF, but a typo → ON). Left as-is
    # precisely because it is unreachable: changing a dormant module's truth table is
    # churn, and wiring the module is a product decision. See the module docstring.
    "navig/core/model_routing.py": {"_coerce_bool"},
    # JSON/env whitelist for license + dev-override data; unknown→False by design. The
    # entitlement path is kept conservative — not a `config set` reader.
    "navig/license/__init__.py": {"_coerce_bool"},
    # MCP tool arguments are model-emitted JSON, not config; the true-set drops "on" by
    # contract and dedicated tests lock these three.
    "navig/mcp/tools/windows.py": {"_coerce_bool"},
    "navig/mcp/tools/filesystem.py": {"_coerce_bool"},
    "navig/mcp/tools/desktop.py": {"_coerce_bool"},
    # Deck HTTP readers of a query-string / JSON body field (already case-insensitive
    # whitelists over API input, not config strings).
    "navig/gateway/deck/routes/telegram_mtproto.py": {"_truthy"},
    "navig/gateway/deck/routes/telegram_manager.py": {"_truthy"},
}


# ── detection ─────────────────────────────────────────────────────────────────


def _body_nodes(fn: ast.AST):
    """Nodes in fn's own body, not descending into nested defs (each is visited separately
    by the outer ``ast.walk``, so a nested helper is judged once on its own)."""
    for stmt in getattr(fn, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield node


def _delegates_to_canonical(fn: ast.AST) -> bool:
    """True if the function references the identifier ``coerce_bool`` (import / call / attr)."""
    for node in _body_nodes(fn):
        if isinstance(node, ast.ImportFrom):
            if any(a.name == "coerce_bool" or a.asname == "coerce_bool" for a in node.names):
                return True
        elif (isinstance(node, ast.Name) and node.id == "coerce_bool") or (
            isinstance(node, ast.Attribute) and node.attr == "coerce_bool"
        ):
            return True
    return False


def _find_violations(tree: ast.AST, rel: str) -> list[str]:
    out: list[str] = []
    allowed = _ALLOWLIST.get(rel, set())
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name not in _HELPER_NAMES:
            continue
        if _delegates_to_canonical(fn) or fn.name in allowed:
            continue
        out.append(
            f"{rel}:{fn.lineno} — {fn.name}() looks like a bool-coercion helper but keeps "
            f"its own truth table. Delegate to navig.core.coerce.coerce_bool, or add it to "
            f"the ALLOWLIST in this test with a one-line reason if it is deliberately "
            f"independent (MCP tool arg / security vocabulary / API query reader)."
        )
    return out


def _scan_roots() -> list[Path]:
    roots = [_CORE_ROOT]
    if _PLUGINS_ROOT.is_dir():
        roots.append(_PLUGINS_ROOT)
    return roots


def _scan(root: Path) -> list[str]:
    violations: list[str] = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root)
        if set(rel.parts) & _EXCLUDE_DIRS:
            continue
        rel_key = f"{root.name}/{rel.as_posix()}"
        if rel_key == _CANONICAL_REL:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        violations += _find_violations(tree, rel_key)
    return violations


# ── the guard ─────────────────────────────────────────────────────────────────


def test_no_unlisted_bool_coercion_helper_survives_in_the_repo():
    """Every idiomatically-named string→bool coercer in core (and plugins) delegates to
    coerce_bool or is an explicitly-justified allowlist entry — no fourth truth table may
    appear silently."""
    violations: list[str] = []
    for root in _scan_roots():
        violations += _scan(root)
    assert not violations, (
        "ad-hoc boolean coercion reintroduced — route it through "
        "navig.core.coerce.coerce_bool (the one config-safe truth table), or allowlist it "
        "with a reason if it is genuinely a different contract:\n  " + "\n  ".join(violations)
    )


# ── the detector must keep its teeth (a blind guard is worse than none) ─────────


def _violations_for(src: str, rel: str = "navig/synthetic.py") -> list[str]:
    return _find_violations(ast.parse(src), rel)


def test_guard_flags_a_new_named_helper():
    src = 'def _truthy(v):\n    return str(v).strip().lower() in ("true", "1", "yes")\n'
    assert len(_violations_for(src)) == 1


def test_guard_accepts_a_delegating_helper():
    src = (
        "def _truthy(v):\n"
        "    from navig.core.coerce import coerce_bool\n"
        "    return coerce_bool(v)\n"
    )
    assert _violations_for(src) == []


def test_guard_accepts_an_allowlisted_helper():
    """An allowlisted (file, function) with its own truth table is exempt — proving the
    ALLOWLIST is honoured for a real entry, not just theoretically."""
    src = 'def _coerce_bool(v):\n    return str(v).lower() in ("true", "1", "yes")\n'
    assert _violations_for(src, "navig/mcp/tools/filesystem.py") == []


def test_guard_ignores_functions_that_are_not_named_helpers():
    """NAME-based scope by design: a yes/no prompt or a query-param reader that happens to
    test string membership is NOT a config coercer and must not be dragged onto coerce_bool.
    Renaming it into `_truthy`/`_coerce_bool` is what (correctly) trips the guard."""
    prompt = 'def _confirm(answer):\n    return answer.lower() in ("y", "yes")\n'
    query = 'def handle(req):\n    return req.query.get("x") in ("1", "true", "yes")\n'
    kill_switch = 'def _shell_enabled(v):\n    return v not in ("false", "0", "off", "disabled")\n'
    assert _violations_for(prompt) == []
    assert _violations_for(query) == []
    assert _violations_for(kill_switch) == []


def test_guard_flags_a_nested_helper_once():
    """A coercer nested in an outer function is flagged on its own, exactly once — the walk
    must not double-count it from the enclosing scope."""
    src = (
        "def outer():\n"
        "    def _truthy(v):\n"
        '        return v in ("true", "false")\n'
        "    return _truthy\n"
    )
    assert len(_violations_for(src)) == 1


# ── the allowlist must not rot ──────────────────────────────────────────────────


def test_allowlist_has_no_stale_entries():
    """Each allowlisted (file, function) must still exist AND still carry a helper name — an
    exemption pointing at a deleted/renamed function is a silent hole in the guard."""
    stale: list[str] = []
    for rel_key, funcs in _ALLOWLIST.items():
        root = _CORE_ROOT if rel_key.startswith("navig/") else _PLUGINS_ROOT
        path = root.parent / rel_key
        if not path.is_file():
            stale.append(f"{rel_key} (file missing)")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        present = {
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in _HELPER_NAMES
        }
        for func in funcs - present:
            stale.append(f"{rel_key}::{func}")
    assert not stale, "stale ALLOWLIST entries — remove or fix them:\n  " + "\n  ".join(stale)


def test_canonical_module_is_present_and_excluded():
    """The guard is meaningless if coerce_bool has vanished; and coerce.py must be excluded
    (it DEFINES the canonical helper)."""
    coerce_py = _CORE_ROOT / "core" / "coerce.py"
    assert coerce_py.is_file(), "canonical navig/core/coerce.py is gone"
    assert "def coerce_bool" in coerce_py.read_text(encoding="utf-8")


def test_scan_covers_plugins_when_present():
    roots = _scan_roots()
    assert _CORE_ROOT in roots
    if _PLUGINS_ROOT.is_dir():
        assert _PLUGINS_ROOT in roots
