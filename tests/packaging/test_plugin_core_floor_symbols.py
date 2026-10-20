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


# ---------------------------------------------------------------------------
# Every plugin standalone vs. nothing found
# ---------------------------------------------------------------------------


def _plugin(root: Path, name: str, deps: list[str]) -> None:
    d = root / name
    d.mkdir(parents=True)
    quoted = ", ".join(f'"{x}"' for x in deps)
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0"\ndependencies = [{quoted}]\n',
        encoding="utf-8",
    )


def test_every_plugin_standalone_is_a_pass_not_a_vacuous_failure(guard, tmp_path, monkeypatch, capsys):
    """Once no plugin declares navig there is no floor to hold — the goal, not a broken guard."""
    _plugin(tmp_path, "navig-a", ["navig-sdk>=2.0"])
    _plugin(tmp_path, "navig-b", ["requests"])
    monkeypatch.setattr(guard, "PLUGINS", tmp_path)
    assert guard.main([]) == 0
    assert "all 2 plugin(s) are standalone" in capsys.readouterr().out


def test_no_plugin_found_still_fails(guard, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(guard, "PLUGINS", tmp_path)
    assert guard.main([]) == 1
    assert "no plugin found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Stage A1 — the classification gap this guard was rewritten to close
# ---------------------------------------------------------------------------


def _sourced_plugin(root: Path, name: str, deps: list[str], source: str) -> Path:
    """A plugin with both metadata AND source, so its imports are discoverable."""
    d = root / name
    d.mkdir(parents=True)
    quoted = ", ".join(f'"{x}"' for x in deps)
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0"\ndependencies = [{quoted}]\n',
        encoding="utf-8",
    )
    (d / "mod.py").write_text(source, encoding="utf-8")
    return d


def test_importing_core_without_a_floor_is_reported_but_not_failed(
    guard, tmp_path, monkeypatch, capsys
):
    """The classification gap, closed the right way round.

    Declaring no `navig` used to fold a plugin straight into "standalone" with its core
    imports never counted. The fix counts them and NAMES the plugin — but it does not
    fail, because `mode = "standalone"` is a promise two other guards actually test
    (`check_plugins_publishable` at import time, and the empirical
    `test_standalone_plugins_run_without_navig`, which blocks `navig` in a fresh
    interpreter and runs the console script). Failing here would put this guard in charge
    of a promise it has no way to test, and would block publishing over an assertion
    already made elsewhere.
    """
    _sourced_plugin(
        tmp_path, "navig-a", ["navig-sdk>=2.0"],
        "from navig.core.coerce import coerce_bool\n",
    )
    monkeypatch.setattr(guard, "PLUGINS", tmp_path)
    assert guard.main([]) == 0
    out = capsys.readouterr().out
    # Counted and named…
    assert "STANDALONE, NOT FLOORED" in out
    assert "navig-a" in out
    assert "1 standalone-declared" in out
    # …with the hand-off spelled out rather than implied…
    assert "test_standalone_plugins_run_without_navig" in out
    assert "RESIDUAL GAP" in out
    # …and it must not be laundered into the clean-standalone bucket.
    assert "0 standalone-clean" in out


def test_the_coverage_line_always_names_what_was_not_verified(
    guard, tmp_path, monkeypatch, capsys
):
    """The reader must see the split, not just the verdict — otherwise a pass is
    indistinguishable from a pass over nothing."""
    _sourced_plugin(tmp_path, "navig-loose", ["navig-sdk>=2.0"], "import navig.core\n")
    _plugin(tmp_path, "navig-plain", ["requests"])
    monkeypatch.setattr(guard, "PLUGINS", tmp_path)
    guard.main([])
    out = capsys.readouterr().out
    assert "coverage:" in out
    assert "1 standalone-declared" in out
    assert "1 standalone-clean" in out


def test_an_unresolvable_floor_does_not_erase_the_other_plugins(
    guard, tmp_path, monkeypatch, capsys
):
    """One floor that cannot be fetched (offline, or a version never published) used to
    `return 0` for the whole run — one plugin's problem ended the verdict for all."""
    _plugin(tmp_path, "navig-floored", ["navig>=9.9"])
    _sourced_plugin(tmp_path, "navig-loose", ["navig-sdk>=2.0"], "import navig.core\n")
    monkeypatch.setattr(guard, "PLUGINS", tmp_path)
    monkeypatch.setattr(guard, "_navig_surface", lambda version: None)

    rc = guard.main([])
    out = capsys.readouterr().out
    # Nothing here is a failure, so this must not be red — but BOTH must be visible.
    assert rc == 0
    assert "SKIPPED" in out                  # the unresolvable floor is named, not swallowed
    assert "navig-floored" in out
    assert "navig-loose" in out              # …and the other plugin is still reported
    assert "NO FLOOR VERIFIED" in out


def test_a_declared_floor_still_reaches_a_real_verdict(
    guard, tmp_path, monkeypatch, capsys
):
    """The original job, still intact: a plugin that declares a floor is checked against
    a wheel, and a symbol core does not ship is a finding."""
    _sourced_plugin(
        tmp_path, "navig-floored", ["navig>=3.0"],
        "from navig.store.contacts import create_schema\n",
    )
    monkeypatch.setattr(guard, "PLUGINS", tmp_path)
    monkeypatch.setattr(
        guard, "_navig_surface",
        lambda version: ({"navig.store.contacts"}, {"navig.store.contacts": {"OTHER"}}),
    )
    assert guard.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL plugin core floor" in out
    assert "create_schema" in out
    assert "1 floor-verified" in out


def test_a_floor_that_holds_is_still_a_pass(guard, tmp_path, monkeypatch, capsys):
    _sourced_plugin(
        tmp_path, "navig-ok", ["navig>=3.0"],
        "from navig.store.contacts import create_schema\n",
    )
    monkeypatch.setattr(guard, "PLUGINS", tmp_path)
    monkeypatch.setattr(
        guard, "_navig_surface",
        lambda version: ({"navig.store.contacts"}, {"navig.store.contacts": {"create_schema"}}),
    )
    assert guard.main([]) == 0
    out = capsys.readouterr().out
    assert "OK plugin core floor: 1 plugin(s)" in out
    assert "UNVERIFIED" not in out
