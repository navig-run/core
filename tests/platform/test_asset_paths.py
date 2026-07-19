"""No runtime asset may SILENTLY escape the navig package.

The bug class this closes
-------------------------
A module that locates an asset by counting ``.parent``s out of its own ``__file__``
can walk *outside* the ``navig`` package::

    Path(__file__).parent.parent.parent / "store" / "skills"   # navig/commands/ → <repo>/core/

setuptools cannot ship a directory outside the package. Such a path therefore resolves
perfectly in a dev checkout and is **absent from every wheel** — and in practice each one
degraded *silently* rather than failing, so nobody noticed:

  * the built-in content store (137 skills, 35 prompts, …) was missing from every published
    wheel — `navig 2.8.0` on PyPI shipped 0 of it (fixed: it now lives in ``navig/builtin``);
  * `navig skill list` showed zero builtin skills when installed;
  * the AHK adapter had no primitives/workflows;
  * `navig net speedtest` raised, because its worker was in neither the wheel nor the sdist.

This test walks the AST of every module under ``navig/`` and fails on **any new** file-anchored
path that escapes the package. Escapes that are genuinely intentional are listed in
``ALLOWED_ESCAPES`` with a reason — the point is that adding one is a deliberate, reviewed act
rather than an accident nobody sees until a user reports an empty product.

Fixing an escape? Delete its entry — the test also fails on a *stale* allowlist entry, so the
list cannot rot into a rubber stamp.
"""

from __future__ import annotations

import ast
from pathlib import Path

import navig

PKG_ROOT = Path(navig.__file__).resolve().parent


# Modules that legitimately reach outside the package, and why. These are NOT asset loads
# that a user depends on: they either detect a source checkout on purpose, or degrade with
# an explicit, actionable message when the path is absent.
ALLOWED_ESCAPES: dict[str, str] = {
    # ── source-checkout detection: reaching outside the package IS the feature ──
    "commands/update.py": "src_dir — DETECTING a source checkout is the point (update-from-source).",
    "commands/upgrade.py": "src_dir — same source-checkout detection as commands/update.py.",
    "update/lifecycle.py": "src_dir — same source-checkout detection as commands/update.py.",
    "update/checker.py": "src_dir — same source-checkout detection as commands/update.py.",
    "commands/migrate.py": "scripts/ — one-off repo migration helpers; source checkout only.",
    "__init__.py": "pyproject.toml — version fallback when running from source (metadata otherwise).",
    "license/__init__.py": (
        "tools/dev_license.py — detects the DEV licence-override tool, which is excluded from "
        "the wheel on purpose. Absent == not a dev checkout, which is the correct answer."
    ),
    "commands/agent.py": ".env — dev convenience; a repo-root .env is a source-checkout concept.",
    "commands/onboard.py": ".env — dev convenience; same as commands/agent.py.",
    # ── deliberately degrade, with a clear message or a correct fallback ──
    "commands/config.py": (
        "config/defaults.yaml — best-effort source attribution for `navig config show` only "
        "(guarded by .exists() + try/except: 'failure is non-critical'). The real defaults are "
        "Python constants; ConfigManager never reads this file."
    ),
    "commands/desktop.py": (
        "host/internal/desktop/agent.py — the desktop agent backend is a source-tree host "
        "component, not Python package data. Now guarded by an existence check that says so."
    ),
    "commands/matrix.py": (
        "deploy/synapse — Matrix/Synapse deployment templates. Explicitly source-checkout/VPS "
        "only: prints 'Deploy dir not found — run from the repo root' and exits."
    ),
    "commands/tray.py": (
        "scripts/navig_tray.py — the tray scripts are not in the repo at all; every entry point "
        "checks .exists() and reports 'Tray script not found'. Dead feature, honest failure."
    ),
    "cli/registry.py": (
        "generated/commands.json — a build-time CLI-schema cache. Absent when installed, and "
        "_load_generated_manifest() returns None so get_schema() falls back to runtime "
        "introspection. Costs startup time; never wrong."
    ),
    "gateway/deck/routes/static_assets.py": (
        "deck-static / navig-deck build output — the compiled deck bundle is delivered by the "
        "installer, not the wheel; every candidate is probed with .exists()."
    ),
    # ── dead references to a `workflows/` tree that does not exist in the repo ──
    # Both former workflow-engine escapes are now fixed and REMOVED from this list (a stale
    # entry fails the no-stale-entries guard, which is how each fix was surfaced):
    #   * core/automation_engine.py — the workflow LOADER, fixed to config_dir()/workflows (#189);
    #   * core/evolution/workflow.py — the workflow WRITER (the AI evolver), fixed the same way
    #     here. It was never "dead": the evolver writes, the automation engine loads/runs. Its
    #     old package-relative dir escaped the wheel AND didn't match the loader, so a generated
    #     workflow was unreachable (and on a real install the save silently failed).
    "daemon/entry.py": "project_root — source-checkout layout probing for the supervised daemon.",
    "daemon/telegram_worker.py": "project_root — same source-checkout probing as daemon/entry.py.",
}


def _levels_up(node: ast.AST) -> int | None:
    """How many ``.parent`` steps above its own file does this expression resolve to?

    ``Path(__file__)`` → 0 (the file itself) · ``.parent`` → 1 (its directory) ·
    ``.parents[k]`` → +k+1 (``parents[0]`` *is* ``.parent``) · ``.resolve()`` → unchanged.
    Returns None when the expression is not anchored at ``Path(__file__)``.
    """
    # Path(__file__) — and pathlib.Path(__file__), which an earlier version of this
    # detector missed, hiding commands/matrix.py entirely.
    if isinstance(node, ast.Call):
        func = node.func
        is_path_ctor = (isinstance(func, ast.Name) and func.id == "Path") or (
            isinstance(func, ast.Attribute) and func.attr == "Path"
        )
        if is_path_ctor:
            if (
                len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "__file__"
            ):
                return 0
            return None
        # <expr>.resolve() / <expr>.absolute()  — level unchanged
        if isinstance(func, ast.Attribute) and func.attr in ("resolve", "absolute"):
            return _levels_up(func.value)
        return None
    # <expr>.parent
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = _levels_up(node.value)
        return None if base is None else base + 1
    # <expr>.parents[k]
    if isinstance(node, ast.Subscript):
        value = node.value
        if isinstance(value, ast.Attribute) and value.attr == "parents":
            base = _levels_up(value.value)
            idx = node.slice
            if base is not None and isinstance(idx, ast.Constant) and isinstance(idx.value, int):
                return base + idx.value + 1
    return None


def _module_depth(py: Path) -> int:
    """Directories between the package root and this module (navig/commands/net.py → 1)."""
    return len(py.resolve().relative_to(PKG_ROOT).parts) - 1


def _escapes(py: Path) -> list[tuple[int, int]]:
    """(lineno, levels_up) for every file-anchored expression that leaves the package.

    A module `d` directories deep reaches the package root at `d + 1` levels up, so anything
    at `d + 2` or more is outside `navig/`.
    """
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover — not our concern here
        return []
    limit = _module_depth(py) + 1
    out: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        level = _levels_up(node)
        if level is not None and level > limit:
            out.append((getattr(node, "lineno", 0), level))
    return out


def _scan() -> dict[str, list[tuple[int, int]]]:
    found: dict[str, list[tuple[int, int]]] = {}
    for py in sorted(PKG_ROOT.rglob("*.py")):
        if "__pycache__" in py.parts or "builtin" in py.relative_to(PKG_ROOT).parts[:1]:
            continue  # builtin/ is shipped *content* (tool scripts), not navig source
        hits = _escapes(py)
        if hits:
            # keep only the outermost expression per line (the longest .parent chain)
            per_line: dict[int, int] = {}
            for lineno, level in hits:
                per_line[lineno] = max(per_line.get(lineno, 0), level)
            found[py.relative_to(PKG_ROOT).as_posix()] = sorted(per_line.items())
    return found


def test_no_new_runtime_asset_escapes_the_package() -> None:
    """The guard: a NEW file-anchored path that leaves navig/ fails here, loudly."""
    found = _scan()
    unexpected = {m: hits for m, hits in found.items() if m not in ALLOWED_ESCAPES}
    assert not unexpected, (
        "These modules resolve a path OUTSIDE the navig package by counting .parent steps.\n"
        "setuptools cannot ship such a path: it works in a dev checkout and is ABSENT from every "
        "wheel, usually degrading silently.\n"
        "Load the asset via navig.platform.paths.builtin_store_dir() (content ships in "
        "navig/builtin/), or — if the escape is deliberate — add it to ALLOWED_ESCAPES with a "
        "reason.\n\n"
        + "\n".join(f"  {m}: {hits}" for m, hits in sorted(unexpected.items()))
    )


def test_allowlist_has_no_stale_entries() -> None:
    """A fixed escape must be removed from the allowlist, so it can't rot into a rubber stamp."""
    found = _scan()
    stale = sorted(set(ALLOWED_ESCAPES) - set(found))
    assert not stale, (
        "These modules no longer escape the package — delete their ALLOWED_ESCAPES entries:\n"
        + "\n".join(f"  {m}" for m in stale)
    )


def test_the_guard_actually_detects_an_escape(tmp_path) -> None:
    """A guard that cannot fail is worthless — prove the detector on a known-bad module.

    Reproduces the real bug: navig/commands/skills.py reaching <repo>/core/store/skills.
    """
    bad = PKG_ROOT / "commands" / "_guard_probe.py"  # 1 dir deep → package root is 2 levels up
    src = 'from pathlib import Path\nD = Path(__file__).parent.parent.parent / "store" / "skills"\n'
    tmp = tmp_path / "probe.py"
    tmp.write_text(src, encoding="utf-8")

    # depth math is driven by the module's location, so evaluate as if it lived at `bad`
    limit = _module_depth(bad) + 1          # == 2
    tree = ast.parse(src)
    levels = [lv for n in ast.walk(tree) if (lv := _levels_up(n)) is not None]
    assert max(levels) == 3                  # .parent × 3
    assert max(levels) > limit               # → escapes, and would be reported

    # …and a correct in-package resolution does NOT trip it
    ok = 'from pathlib import Path\nD = Path(__file__).parent.parent / "data" / "x.json"\n'
    levels_ok = [lv for n in ast.walk(ast.parse(ok)) if (lv := _levels_up(n)) is not None]
    assert max(levels_ok) <= limit
