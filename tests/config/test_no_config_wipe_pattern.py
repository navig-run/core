"""The config-wipe read-modify-write anti-pattern must never come back.

`cfg = safe_load_yaml(path) or {}` then `atomic_write_yaml(cfg, path)` silently DELETES a
populated config when the read fails transiently (a lock / half-written `os.replace`):
`safe_load_yaml` returns None for "unreadable" exactly as for "empty", so `or {}` collapses
to a near-empty dict that the write then persists over `deck.api_key` and everything else.
The RAW form — `open(path)` + `yaml.safe_load(f) or {}` — is the same class: it crashes on a
lock (loud) but still wipes on a half-written file that parses to None.

Both were killed across every direct caller (PRs #303/#310 for `safe_load_yaml`; a later PR
for the six raw `yaml.safe_load` sites) by routing them through `yaml_io.load_yaml_for_update`,
which REFUSES an unreadable-but-populated file instead of handing back `{}`. This is the lock
on that door: a source-level guard that fails the build if any function ever again reads a
config with `safe_load_yaml(...) or {}` OR `yaml.safe_load(...) or {}` AND writes that same
variable back with `atomic_write_yaml`.

Precision matters — a bare grep would flag the legitimate READ-ONLY `... or {}` sites (which
never write back) and any unrelated function that happens to reuse the name `cfg`. So the
guard is data-flow aware: it flags a variable ONLY when the *same* name, tainted by
`safe_load_yaml(...)`/`yaml.safe_load(...) or {}`, is later passed to `atomic_write_yaml`
*within the same function scope* (nested closures scoped independently — that is where
onboarding's persist helpers live).
"""

from __future__ import annotations

import ast
from pathlib import Path

# Pure static analysis — locate the sources by path, never import them. Keeps the guard
# fast (no package import) and usable as a pre-push gate, and independent of whether the
# tree currently imports cleanly.  core/tests/config/<this> -> parents[2]==core, [3]==repo.
_CORE_ROOT = Path(__file__).resolve().parents[2] / "navig"
_PLUGINS_ROOT = Path(__file__).resolve().parents[3] / "plugins"

# Dirs whose .py files are not shipped runtime source: tests may hold the pattern
# intentionally (as string fixtures); the rest is vendored / build output.
_EXCLUDE_DIRS = {"tests", "test", ".venv", "node_modules", ".dev", "build", "dist", "__pycache__"}


def _scan_roots() -> list[Path]:
    """core/navig, plus plugins/ when present — core also ships standalone, without it."""
    roots = [_CORE_ROOT]
    if _PLUGINS_ROOT.is_dir():
        roots.append(_PLUGINS_ROOT)
    return roots


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


# Both the navig helper and the raw PyYAML call collapse a failed read to None.
_TAINTING_READS = {"safe_load_yaml", "safe_load"}


def _is_safe_load_or_default(value: ast.AST) -> bool:
    """Match `safe_load_yaml(...) or <default>` / `yaml.safe_load(...) or <default>`."""
    return (
        isinstance(value, ast.BoolOp)
        and isinstance(value.op, ast.Or)
        and _call_name(value.values[0]) in _TAINTING_READS
    )


def _own_nodes(fn: ast.AST):
    """Yield nodes in fn's body, NOT descending into nested function/lambda scopes.

    Each nested function is visited on its own (ast.walk finds it separately), so a
    read-only `safe_load_yaml(...) or {}` in one closure is never conflated with an
    `atomic_write_yaml` in a sibling closure of the same enclosing function.
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


def _is_writeback_call(node: ast.Call) -> bool:
    """A call that persists its dict argument back to disk.

    Two shapes, because a read-modify-write can serialise EITHER way and both wipe:
      * ``atomic_write_yaml(cfg, path)`` — the canonical helper, and
      * the hand-rolled ``yaml.safe_dump(cfg, stream)`` / ``yaml.dump(cfg, stream)`` +
        ``os.replace`` — a dump with **>=2 positional args** writes to a file object.

    The second shape used to slip the guard entirely: ``navig suggest``'s quick-action
    writer read ``yaml.safe_load(f) or {}`` then saved with ``yaml.safe_dump(actions, fh)``,
    so a half-written read collapsed to ``{}`` and wiped every other action — invisibly to
    a guard that only watched ``atomic_write_yaml``. A ONE-arg ``safe_dump(cfg)`` returns a
    STRING (not a write) and is deliberately NOT matched, so read-only "dump to preview"
    code is never falsely flagged.
    """
    name = _call_name(node)
    if name == "atomic_write_yaml":
        return True
    return name in {"safe_dump", "dump"} and len(node.args) >= 2


def _find_violations(tree: ast.AST, rel: str) -> list[str]:
    """Return human-readable violation strings for one parsed module."""
    out: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        tainted: dict[str, int] = {}
        writes: list[tuple[str, int]] = []
        for node in _own_nodes(fn):
            if isinstance(node, ast.Assign) and _is_safe_load_or_default(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        tainted[target.id] = node.lineno
            elif isinstance(node, ast.Call) and _is_writeback_call(node):
                for arg in node.args:
                    if isinstance(arg, ast.Name):
                        writes.append((arg.id, node.lineno))
        for name, lineno in writes:
            if name in tainted:
                out.append(
                    f"{rel}:{lineno} — {fn.name}() reads '{name}' with "
                    f"`safe_load_yaml(...) or {{}}` (line {tainted[name]}) then writes it "
                    f"back to disk (atomic_write_yaml / yaml.safe_dump). "
                    f"Use yaml_io.load_yaml_for_update instead."
                )
    return out


def _scan(root: Path) -> list[str]:
    violations: list[str] = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root)
        if set(rel.parts) & _EXCLUDE_DIRS:
            continue  # tests / vendored / build output — not shipped runtime source
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue  # a template / non-source file can't execute the pattern anyway
        violations += _find_violations(tree, f"{root.name}/{rel.as_posix()}")
    return violations


# ── the guard ────────────────────────────────────────────────────────────────


def test_no_config_wipe_read_modify_write_survives_in_the_repo():
    """No function in core OR any plugin may read a config with `safe_load_yaml(...) or {}`
    / `yaml.safe_load(...) or {}` and write that same dict back — that silently wipes the
    file on a transient read lock."""
    violations: list[str] = []
    for root in _scan_roots():
        violations += _scan(root)
    assert not violations, (
        "config-wipe read-modify-write pattern reintroduced — route the read through "
        "navig.core.yaml_io.load_yaml_for_update (it refuses an unreadable-but-populated "
        "file instead of returning {}):\n  " + "\n  ".join(violations)
    )


# ── the guard must actually be able to see the pattern ───────────────────────
#
# A guard that can no longer detect anything is worse than none: it reports green over an
# unchecked tree. These pin the detector's teeth so a refactor can't silently blunt it.


def test_the_scan_covers_plugins_not_just_core():
    """A plugin (navig-github/navig-email) reintroducing the pattern was the reason this
    scope was widened — so if plugins/ exists it MUST be scanned, never silently dropped."""
    roots = _scan_roots()
    assert _CORE_ROOT in roots
    if _PLUGINS_ROOT.is_dir():
        assert _PLUGINS_ROOT in roots, "plugins/ present but not scanned — green over an unchecked tree"


def test_guard_flags_the_pattern_including_inside_a_nested_closure():
    src = (
        "def outer():\n"
        "    def persist(p):\n"
        "        cfg = safe_load_yaml(p) or {}\n"
        "        cfg['x'] = 1\n"
        "        atomic_write_yaml(cfg, p)\n"
    )
    assert len(_find_violations(ast.parse(src), "synthetic.py")) == 1


def test_guard_flags_the_raw_yaml_safe_load_variant():
    """The raw `open()` + `yaml.safe_load(f) or {}` form is the same class and must also
    be caught — six such sites (config/agent/proactive/template/modes) were converted."""
    src = (
        "def persist(p):\n"
        "    with open(p) as f:\n"
        "        data = yaml.safe_load(f) or {}\n"
        "    data['x'] = 1\n"
        "    atomic_write_yaml(data, p)\n"
    )
    assert len(_find_violations(ast.parse(src), "synthetic.py")) == 1


def test_guard_ignores_a_read_only_site():
    src = (
        "def show(p):\n"
        "    cfg = safe_load_yaml(p) or {}\n"
        "    return cfg.get('x')\n"
    )
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_guard_accepts_the_correct_load_yaml_for_update_form():
    src = (
        "def persist(p):\n"
        "    cfg = load_yaml_for_update(p)\n"
        "    cfg['x'] = 1\n"
        "    atomic_write_yaml(cfg, p)\n"
    )
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_guard_flags_the_hand_rolled_safe_dump_writeback():
    """The hand-rolled `yaml.safe_dump(cfg, fh)` + os.replace write (NOT atomic_write_yaml)
    is the same wipe class and must be caught — this is exactly how `navig suggest`'s
    quick-action writer slipped the guard before it was widened."""
    src = (
        "def save(p, tmp):\n"
        "    with open(p) as f:\n"
        "        cfg = yaml.safe_load(f) or {}\n"
        "    cfg['x'] = 1\n"
        "    with open(tmp, 'w') as fh:\n"
        "        yaml.safe_dump(cfg, fh)\n"
    )
    assert len(_find_violations(ast.parse(src), "synthetic.py")) == 1


def test_guard_ignores_safe_dump_to_a_string():
    """`s = yaml.safe_dump(cfg)` (one positional arg → a STRING, not a file write) is NOT a
    write-back — read-only "serialise for preview" code must never be flagged."""
    src = (
        "def preview(p):\n"
        "    cfg = safe_load_yaml(p) or {}\n"
        "    return yaml.safe_dump(cfg)\n"
    )
    assert _find_violations(ast.parse(src), "synthetic.py") == []


def test_guard_does_not_conflate_two_sibling_closures():
    """A read-only closure and a writer closure that both use `cfg` must NOT combine into
    a false positive — the exact scoping bug that would make the guard cry wolf."""
    src = (
        "def outer():\n"
        "    def show(p):\n"
        "        cfg = safe_load_yaml(p) or {}\n"
        "        return cfg.get('x')\n"
        "    def persist(p):\n"
        "        cfg = load_yaml_for_update(p)\n"
        "        atomic_write_yaml(cfg, p)\n"
    )
    assert _find_violations(ast.parse(src), "synthetic.py") == []
