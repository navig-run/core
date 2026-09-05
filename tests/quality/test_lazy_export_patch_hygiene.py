"""Patching a lazily re-exported name at the submodule alone poisons it forever.

`navig/providers/__init__.py` resolves a lazy export through a module-level
``__getattr__`` and then **caches the value into the package's ``globals()``** so
later reads bypass the hook. That is correct in production — nothing swaps a
submodule attribute at runtime — but it makes a test patch irreversible:

    monkeypatch.setattr("navig.providers.auth.AuthProfileManager", _Fake)
    ...                                   # something reads navig.providers.AuthProfileManager
                                          # -> __getattr__ resolves the FAKE and caches it
    # teardown restores navig.providers.auth.AuthProfileManager
    # navig.providers.AuthProfileManager is STILL _Fake, for the rest of the session

Measured exactly that way::

    auth module restored  : True
    package still poisoned: True
    has add_api_key       : False

The damage lands on whatever runs next. `tests/quality/test_instance_method_contract.py`
resolves `AuthProfileManager` through the package, so after `tests/agent` had run it
reported **13 false findings** — every one of `auth.add_api_key` / `.save` / `.store` /
`.remove_profile` in `commands/ai.py` and `providers/connect.py`, all of which exist.
The guard was red on `main` in any combined run and green in isolation, which reads
like flake and trains people to ignore it.

Fix: patch the **package** name first (so monkeypatch records the real object as the
original), then the submodule if the code under test imports from there.
"""
from __future__ import annotations

import ast
from pathlib import Path

CORE_TESTS = Path(__file__).resolve().parents[1]


def _lazy_exported_names() -> set[str]:
    """Names `navig.providers` re-exports through its caching `__getattr__`."""
    import navig.providers as providers

    return set(getattr(providers, "_LAZY", {}))


def _setattr_targets(fn: ast.AST) -> list[tuple[str, int]]:
    """Every `monkeypatch.setattr("dotted.path", ...)` inside one function."""
    targets: list[tuple[str, int]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "setattr"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "monkeypatch"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            targets.append((first.value, node.lineno))
    return targets


def _functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def test_a_lazily_reexported_name_is_never_patched_at_the_submodule_alone() -> None:
    lazy = _lazy_exported_names()
    assert lazy, "navig.providers exposes no lazy names; this guard is inert"

    findings: list[str] = []

    for path in sorted(CORE_TESTS.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue

        for fn in _functions(tree):
            targets = _setattr_targets(fn)
            if not targets:
                continue
            patched = {t for t, _ in targets}

            for target, lineno in targets:
                parts = target.split(".")
                # navig.providers.<submodule>.<Name>  — 4+ parts, name is lazy
                if len(parts) < 4:
                    continue
                if parts[0] != "navig" or parts[1] != "providers":
                    continue
                name = parts[-1]
                if name not in lazy:
                    continue
                if f"navig.providers.{name}" in patched:
                    continue  # the package name is patched too — reversible
                rel = path.relative_to(CORE_TESTS.parent).as_posix()
                findings.append(
                    f"{rel}:{lineno}: patches {target!r} but not "
                    f"'navig.providers.{name}'"
                )

    assert not findings, (
        "`navig.providers.__getattr__` caches a resolved lazy export into the package's "
        "globals(), so patching only the submodule leaves the package holding the fake "
        "for the rest of the pytest session — monkeypatch cannot undo what it never "
        "recorded. Patch the package name FIRST, then the submodule:\n\n"
        "    monkeypatch.setattr('navig.providers.<Name>', fake)\n"
        "    monkeypatch.setattr('navig.providers.<submodule>.<Name>', fake)\n\n"
        + "\n".join(f"  {f}" for f in findings)
    )


def test_the_caching_that_makes_this_dangerous_still_exists() -> None:
    """Anti-vacuity: if `navig.providers` ever stops caching lazy exports into
    globals() this guard becomes unnecessary — and should be deleted deliberately,
    not left passing over a condition that no longer holds."""
    source = (
        Path(__file__).resolve().parents[2] / "navig" / "providers" / "__init__.py"
    ).read_text(encoding="utf-8")

    assert "globals()[name] = value" in source, (
        "navig/providers/__init__.py no longer caches lazy exports into globals(); "
        "re-check whether this guard is still needed before deleting it"
    )
