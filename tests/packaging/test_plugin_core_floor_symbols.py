"""
The core-floor guard must catch a missing NAME, not only a missing module.

The bug it missed, found by trying to publish navig-contacts: the plugin
imports `create_schema`, `HARD_KINDS` and two route maps from
`navig.store.contacts`. That module has existed for releases, so the guard --
which resolved module paths against the wheel's file list -- said the floor
held. Every one of those names was added to core last week and is in no
published wheel, so `pip install navig-contacts` would have produced a package
that imports and then dies on first use.

A published wheel is forever, which is why this is worth a test.

These exercise the parsing halves only. The guard's wheel download is
deliberately untested here: a test that reaches PyPI fails on a train.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (Path(__file__).resolve().parents[3] / "scripts"
           / "check_plugin_core_floor.py")


@pytest.fixture(scope="module")
def guard():
    if not _SCRIPT.is_file():                      # pragma: no cover
        pytest.skip(f"{_SCRIPT} not present")
    spec = importlib.util.spec_from_file_location("_floor_guard", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_floor_guard"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# What a published module offers
# ---------------------------------------------------------------------------

def test_the_obvious_definitions_count(guard):
    surface = guard._module_names(
        "CONST = 1\n"
        "OTHER: int = 2\n"
        "def helper(): ...\n"
        "async def slow(): ...\n"
        "class Thing: ...\n"
    )
    assert surface == {"CONST", "OTHER", "helper", "slow", "Thing"}


def test_a_re_export_counts(guard):
    """`from navig.x import y` works when x imported y itself."""
    surface = guard._module_names("from .other import shared\nimport os\n")
    assert "shared" in surface
    assert "os" in surface


def test_an_aliased_import_counts_under_its_alias(guard):
    surface = guard._module_names("from .other import shared as public\n")
    assert "public" in surface
    assert "shared" not in surface


def test_a_conditional_definition_still_counts(guard):
    """
    Guarded definitions are real. Reporting them missing would be a false
    alarm, and a guard that cries wolf gets switched off.
    """
    surface = guard._module_names(
        "import sys\n"
        "if sys.platform == 'win32':\n"
        "    def only_on_windows(): ...\n"
        "try:\n"
        "    from .fast import boost\n"
        "except ImportError:\n"
        "    boost = None\n"
    )
    assert {"only_on_windows", "boost"} <= surface


def test_a_module_that_builds_its_surface_at_runtime_is_not_judged(guard):
    """A module-level __getattr__ makes the static surface meaningless."""
    assert guard._module_names("def __getattr__(name): return 1\n") is None


def test_a_star_import_makes_the_surface_undecidable(guard):
    assert guard._module_names("from .everything import *\n") is None


def test_unparseable_source_is_not_judged(guard):
    assert guard._module_names("def (:\n") is None


def test_a_nested_definition_is_not_importable(guard):
    """`from m import inner` fails when inner is defined inside a function."""
    surface = guard._module_names("def outer():\n    def inner(): ...\n")
    assert surface == {"outer"}


# ---------------------------------------------------------------------------
# What a plugin asks for
# ---------------------------------------------------------------------------

def test_imported_symbols_are_collected_with_their_location(guard, tmp_path):
    (tmp_path / "mod.py").write_text(
        "from navig.store.contacts import create_schema, HARD_KINDS\n",
        encoding="utf-8")
    found = guard._imported_core_symbols(tmp_path)
    assert ("navig.store.contacts", "create_schema") in found
    assert ("navig.store.contacts", "HARD_KINDS") in found
    assert found[("navig.store.contacts", "create_schema")] == "mod.py:1"


def test_a_lazily_imported_symbol_is_collected_too(guard, tmp_path):
    """
    An import inside a function is the worse case, not the safer one: it fails
    at the moment the feature runs rather than at install time.
    """
    (tmp_path / "mod.py").write_text(
        "def go():\n    from navig.store.contacts import create_schema\n",
        encoding="utf-8")
    assert ("navig.store.contacts", "create_schema") in guard._imported_core_symbols(tmp_path)


def test_relative_and_non_navig_imports_are_ignored(guard, tmp_path):
    (tmp_path / "mod.py").write_text(
        "from .sibling import thing\nfrom os.path import join\n", encoding="utf-8")
    assert guard._imported_core_symbols(tmp_path) == {}


def test_a_star_import_from_navig_is_skipped(guard, tmp_path):
    (tmp_path / "mod.py").write_text("from navig.store import *\n", encoding="utf-8")
    assert guard._imported_core_symbols(tmp_path) == {}


def test_tests_and_build_output_are_not_scanned(guard, tmp_path):
    for junk in ("tests", "build", "__pycache__"):
        (tmp_path / junk).mkdir()
        (tmp_path / junk / "mod.py").write_text(
            "from navig.store.contacts import create_schema\n", encoding="utf-8")
    assert guard._imported_core_symbols(tmp_path) == {}


# ---------------------------------------------------------------------------
# The two halves together -- the shape of the bug that got through
# ---------------------------------------------------------------------------

def test_a_present_module_missing_the_name_is_a_finding(guard, tmp_path):
    (tmp_path / "mod.py").write_text(
        "from navig.store.contacts import create_schema\n", encoding="utf-8")

    published = guard._module_names("class ContactStore: ...\ndef normalize_phone(): ...\n")
    assert "create_schema" not in published, (
        "this is exactly what navig 3.25.0 ships, and why the plugin would break")

    asked = guard._imported_core_symbols(tmp_path)
    assert ("navig.store.contacts", "create_schema") in asked


def test_a_submodule_imported_by_name_is_not_a_missing_symbol(guard):
    """
    `from navig.store import contacts` imports a module, not an attribute of
    one. The guard checks the module list before calling it missing -- without
    that, every package-relative import would be a false alarm.
    """
    package = guard._module_names("")           # an empty __init__.py
    assert package == set()
    # The caller's guard: `navig.store.contacts` IS in the module list, so this
    # never reaches the "name missing" branch.


# ---------------------------------------------------------------------------
# An import guarded by `except ImportError` is optional, not a false floor
# ---------------------------------------------------------------------------


def _write(tmp_path, source: str) -> None:
    import textwrap

    (tmp_path / "mod.py").write_text(textwrap.dedent(source), encoding="utf-8")


@pytest.mark.parametrize("handler", ["ImportError", "ModuleNotFoundError", "(ImportError, OSError)"])
def test_an_import_with_an_absence_fallback_is_not_a_finding(guard, tmp_path, handler):
    _write(tmp_path, f"""
        try:
            from navig.brand_new import helper
        except {handler}:
            def helper(): ...
        """)
    assert guard._imported_core_symbols(tmp_path) == {}
    assert guard._imported_core_modules(tmp_path) == {}


def test_a_broad_except_still_counts(guard, tmp_path):
    """`except Exception` swallows the ImportError AND everything else — the shape that
    makes a missing core feature silently dead. It stays a finding."""
    _write(tmp_path, """
        try:
            from navig.brand_new import helper
        except Exception:
            pass
        """)
    assert ("navig.brand_new", "helper") in guard._imported_core_symbols(tmp_path)


def test_only_the_try_body_is_guarded(guard, tmp_path):
    """An import in the handler or the else-branch is not protected by that handler."""
    _write(tmp_path, """
        try:
            import json
        except ImportError:
            from navig.fallback import a
        else:
            from navig.other import b
        """)
    found = guard._imported_core_symbols(tmp_path)
    assert ("navig.fallback", "a") in found and ("navig.other", "b") in found


def test_a_lazy_guarded_import_inside_a_function_counts_as_guarded(guard, tmp_path):
    _write(tmp_path, """
        def f():
            try:
                from navig.brand_new import helper
            except ImportError:
                return None
            return helper
        """)
    assert guard._imported_core_symbols(tmp_path) == {}
