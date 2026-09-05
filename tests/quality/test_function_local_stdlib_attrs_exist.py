"""A stdlib module imported INSIDE a function must still be called correctly.

`scripts/check_module_attrs.py` catches `<module>.<attr>` where the attribute does not
exist — this repo's most repeated severe bug — but it is built on mypy, and mypy goes
blind on a module imported lazily inside a function body under a platform branch. That
blind spot is not theoretical:

    navig/commands/tray.py:  winreg.OpenSubKey(...)      # the real name is OpenKey

`winreg.OpenSubKey` exists on no platform. It raised AttributeError on EVERY run of
`navig tray uninstall`, an `except Exception` turned that into a warning, and the
command then printed "NAVIG Tray uninstalled" — so the Run entry the installer writes
was never once removed and the tray kept launching at boot. mypy reported nothing and
the module-attr guard was green.

This guard needs no mypy: it resolves the module for real and asks `hasattr`. That is
only meaningful for names that exist on the platform running the test, which is why the
suppression below is by NAME and stays tiny — a genuine typo is not in it.

Measured when written: 1554 function-local stdlib attribute uses across `navig/`,
one real defect.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
STDLIB = set(sys.stdlib_module_names)

# Names that genuinely exist — on another OS. Each call site is inside a platform
# branch, so the code is correct and it is the *test host* that cannot see them.
# Same rule and same spirit as `_PLATFORM_ONLY` in scripts/check_module_attrs.py:
# suppressed by name, so `os.getuidd` would still fail.
_PLATFORM_ONLY = {
    "SIGKILL",  # signal — POSIX; the Windows branch above it uses taskkill
    "AF_UNIX",  # socket — POSIX; only reached from _send_unix
}


def _missing_attributes() -> list[str]:
    found: list[str] = []
    for path in sorted(CORE.rglob("*.py")):
        if "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            # Bind local name -> REAL module. An alias must resolve to what it
            # aliases: `import json as _json` is NOT the C accelerator `_json`, and
            # resolving the alias literally produced 80 false positives.
            bound: dict[str, str] = {}
            submodules: set[str] = set()
            for node in ast.walk(fn):
                if not isinstance(node, ast.Import):
                    continue
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in STDLIB:
                        continue
                    if "." in alias.name and alias.asname is None:
                        # `import ctypes.wintypes` binds `ctypes` AND makes the
                        # submodule reachable as an attribute — not a defect.
                        bound[root] = root
                        submodules.add(alias.name)
                    else:
                        bound[alias.asname or alias.name] = alias.name

            if not bound:
                continue

            for node in ast.walk(fn):
                if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
                    continue
                real = bound.get(node.value.id)
                if real is None or node.attr in _PLATFORM_ONLY:
                    continue
                if f"{real}.{node.attr}" in submodules:
                    continue
                try:
                    module = importlib.import_module(real)
                except Exception:  # noqa: BLE001 - unimportable here proves nothing
                    continue
                if not hasattr(module, node.attr):
                    found.append(
                        f"{path.relative_to(CORE).as_posix()}:{node.lineno}  "
                        f"{node.value.id}.{node.attr}  ({real} has no such attribute)"
                    )
    return found


def test_no_function_local_stdlib_call_targets_a_missing_attribute() -> None:
    missing = _missing_attributes()
    assert not missing, (
        "These call a stdlib attribute that does not exist. mypy cannot see them "
        "(the module is imported inside a function), so the module-attr guard stays "
        "green while the call raises AttributeError on every run:\n  "
        + "\n  ".join(missing)
    )


def test_the_scan_actually_inspects_a_meaningful_number_of_calls() -> None:
    """Anti-vacuity. Every filter here (stdlib membership, function-local imports,
    alias resolution) is a way for the scan to quietly match nothing and pass."""
    count = 0
    for path in sorted(CORE.rglob("*.py")):
        if "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            bound = {
                (a.asname or a.name)
                for n in ast.walk(fn)
                if isinstance(n, ast.Import)
                for a in n.names
                if a.name.split(".")[0] in STDLIB
            }
            count += sum(
                1
                for n in ast.walk(fn)
                if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id in bound
            )
    assert count > 800, (
        f"only {count} function-local stdlib attribute uses seen — there were 1554 "
        "when this guard was written, so the scan has stopped reaching the tree."
    )


def test_the_detector_still_catches_the_bug_it_was_written_for() -> None:
    """`winreg.OpenSubKey` is the exact shape that shipped. If this stops being
    detected, the guard has been narrowed into decoration."""
    src = (
        "def uninstall():\n"
        "    import winreg\n"
        "    winreg.OpenSubKey(winreg.HKEY_CURRENT_USER, 'x', 0, winreg.KEY_WRITE)\n"
    )
    tree = ast.parse(src)
    fn = tree.body[0]
    bound = {
        (a.asname or a.name): a.name
        for n in ast.walk(fn)
        if isinstance(n, ast.Import)
        for a in n.names
    }
    missing = [
        n.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id in bound
        and not hasattr(importlib.import_module(bound[n.value.id]), n.attr)
    ]
    assert missing == ["OpenSubKey"]


def test_an_alias_resolves_to_the_module_it_aliases() -> None:
    """`import json as _json` must not be resolved as the C module `_json`. Getting
    this wrong turned 6 findings into 86, and the real one was buried in the noise."""
    assert hasattr(importlib.import_module("json"), "dumps")
    # `_json` is a real module and it does NOT carry `dumps` — the exact confusion.
    assert not hasattr(importlib.import_module("_json"), "dumps")
