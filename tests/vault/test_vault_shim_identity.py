"""Every extracted vault module must be ALIASED into core, never re-exported.

Why this is a guard and not a style note
----------------------------------------
The vault engine is moving into the standalone ``navig-vault`` package. Core keeps a module
at the old path so ~66 existing import sites do not move, and the only correct way to write
that shim is::

    from navig_vault import types as _impl
    sys.modules[__name__] = _impl

The tempting simplification -- ``from navig_vault.types import *`` -- looks equivalent and is
not. It produces a SECOND module object holding COPIES of the names, and then:

* ``core/tests/conftest.py`` ASSIGNS module globals in the vault package to close SQLite
  handles between test modules (Windows leaks ``vault.db`` handles otherwise). Under a
  star-import shim that assignment lands on the shim while the real module keeps its own
  value, so the reset silently does nothing.
* ~87 tests patch ``navig.vault.core.get_vault`` and friends by dotted path. Patching a copy
  leaves the original live, so the patch appears to work and the real code runs unpatched.
* Private names (``_CONST``, ``_helper``) are not exported by ``*`` at all, and several
  importers reach for them.

Every one of those fails as flakiness somewhere else -- a Windows file lock in an unrelated
module, a test that passes alone and fails in a suite -- rather than as a clean error here.
So the identity is asserted directly.

``navig.vault`` itself is deliberately NOT in this list: aliasing the PACKAGE would inherit
its ``__path__``, so ``import navig.vault.core`` would re-execute the engine under a second
name and produce two ``Vault`` classes, two singletons, and two SQLite connections to one
file. The package ``__init__`` stays a real module; only submodules self-alias.
"""

from __future__ import annotations

import importlib
import sys

import pytest

pytest.importorskip("navig_vault", reason="navig-vault engine not installed")

# (core path, engine path) for every module that has been extracted so far.
EXTRACTED = [
    ("navig.vault.types", "navig_vault.types"),
    ("navig.vault.secret_str", "navig_vault.secret_str"),
    ("navig.vault.totp", "navig_vault.totp"),
    ("navig.vault._constants", "navig_vault._constants"),
    ("navig.vault.crypto", "navig_vault.crypto"),
    ("navig.vault.encryption", "navig_vault.encryption"),
    ("navig.vault.storage", "navig_vault.storage"),
    ("navig.vault.store", "navig_vault.store"),
    ("navig.vault.session", "navig_vault.session"),
    ("navig.vault.provider", "navig_vault.provider"),
    ("navig.vault.validators", "navig_vault.validators"),
    ("navig.vault.core", "navig_vault.core"),
    ("navig.vault.migrate", "navig_vault.migrate"),
    ("navig.vault.logins", "navig_vault.logins"),
    ("navig.vault.resolver", "navig_vault.resolver"),
    ("navig.vault.sessions", "navig_vault.sessions"),
    ("navig.vault.manager", "navig_vault.manager"),
]


@pytest.mark.parametrize(("core_path", "engine_path"), EXTRACTED)
def test_shim_is_the_same_module_object(core_path: str, engine_path: str) -> None:
    core_mod = importlib.import_module(core_path)
    engine_mod = importlib.import_module(engine_path)
    assert core_mod is engine_mod, (
        f"{core_path} is a DIFFERENT module object from {engine_path}. The shim must alias "
        f"(`sys.modules[__name__] = _impl`), not re-export -- a copy breaks dotted "
        f"monkeypatching and the conftest state resets that keep vault.db from leaking "
        f"handles on Windows."
    )
    assert sys.modules[core_path] is sys.modules[engine_path]


@pytest.mark.parametrize(("core_path", "engine_path"), EXTRACTED)
def test_private_names_survive_the_shim(core_path: str, engine_path: str) -> None:
    """A star-import shim drops every underscore-prefixed name. Importers use them."""
    core_mod = importlib.import_module(core_path)
    engine_mod = importlib.import_module(engine_path)
    private = [n for n in vars(engine_mod) if n.startswith("_") and not n.startswith("__")]
    missing = [n for n in private if not hasattr(core_mod, n)]
    assert not missing, f"{core_path} lost private names {missing} -- it is re-exporting, not aliasing"


def test_the_package_itself_is_not_aliased() -> None:
    """navig.vault must stay a real module; aliasing it would duplicate the engine."""
    import navig_vault

    import navig.vault

    assert navig.vault is not navig_vault, (
        "navig.vault has been aliased to navig_vault. That inherits __path__, so "
        "`import navig.vault.core` re-executes the engine under a second name -- two Vault "
        "classes, two singletons, two SQLite connections to one file."
    )


def test_the_list_matches_what_is_actually_shimmed() -> None:
    """A module extracted without being added here would be guarded by nothing.

    Detects the shim by its distinguishing line rather than by importing, so a broken shim
    is still counted (and then fails the identity test above with a useful message).
    """
    from pathlib import Path

    vault_dir = Path(importlib.import_module("navig.vault").__file__).parent
    shimmed = {
        f"navig.vault.{p.stem}"
        for p in vault_dir.glob("*.py")
        if p.stem != "__init__" and "sys.modules[__name__] = _impl" in p.read_text(encoding="utf-8")
    }
    listed = {core for core, _ in EXTRACTED}
    assert shimmed == listed, (
        f"EXTRACTED is out of date. Shimmed on disk but unguarded: {sorted(shimmed - listed)}; "
        f"listed but no longer a shim: {sorted(listed - shimmed)}."
    )
