"""Guard: navig-vault's vendored permission fallback must not drift from core's.

``navig_vault/_compat.py`` carries a standalone copy of
``navig.core.file_permissions.set_owner_only_file_permissions`` so the vault can run
without navig. That copy guards ``vault.db`` (plus its ``-wal``/``-shm`` companions) and
``vault.salt``; when it degrades, the credential inventory and the audit log become
readable to other local users on the machine.

The dangerous property is that **the copy never runs in a navig install** — ``_compat``
delegates, so the fallback executes only standalone. Code that never runs where anyone is
looking is code that rots silently. This is not hypothetical: the sibling
``navig_blackbox/_compat.py`` already carries exactly that bug (its ``_config_dir_fallback``
forgets the system-service branch), and it went unnoticed for the same reason.

So the two are compared on the AST — comments, docstring and the function's own name may
differ, nothing else may. If core's implementation is deliberately changed, change the
vendored copy in the same commit; a security fix that lands on one side only is precisely
what this test exists to prevent.

The module is loaded FROM ITS PATH, not imported. navig-vault is not a dependency of
core and is not installed in CI, so `pytest.importorskip` would make this skip exactly
where it needs to run — and a guard that skips in CI is a guard that does not run. It
skips only when the file is genuinely absent (a checkout without `plugins/`).
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
from pathlib import Path

import pytest

_COMPAT = (
    Path(__file__).resolve().parents[3] / "plugins" / "navig-vault" / "navig_vault" / "_compat.py"
)


def _load_compat():
    """Load navig_vault._compat from disk — no install, no import of the package."""
    if not _COMPAT.is_file():
        pytest.skip(f"{_COMPAT} not present — checkout without plugins/")
    spec = importlib.util.spec_from_file_location("_navig_vault_compat_probe", _COMPAT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _normalised(fn) -> str:
    """AST dump with the docstring and the function name stripped."""
    tree = ast.parse(inspect.getsource(fn).lstrip())
    node = tree.body[0]
    node.name = "_"
    node.body = [
        n
        for n in node.body
        if not (
            isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        )
    ]
    return ast.dump(ast.Module(body=[node], type_ignores=[]))


def test_vendored_permission_fallback_matches_core() -> None:
    navig_vault_compat = _load_compat()
    from navig.core.file_permissions import set_owner_only_file_permissions as core_fn

    vault_fn = navig_vault_compat._set_owner_only_fallback

    assert _normalised(core_fn) == _normalised(vault_fn), (
        "navig_vault/_compat.py::_set_owner_only_fallback has drifted from "
        "navig.core.file_permissions::set_owner_only_file_permissions.\n"
        "That copy is what protects vault.db and vault.salt on a standalone install, and it "
        "never executes when navig is present — so drift here is invisible until it matters. "
        "Update both sides in the same change."
    )


def test_the_windows_branch_did_not_lose_its_teeth() -> None:
    """Belt and braces on the specific details that made core's version correct.

    An AST comparison catches drift between the two, but not both drifting together. These
    three are the ones whose loss is silent: `/inheritance:r` without the `/remove:g` still
    leaves inherited groups; and `text=True` makes icacls' OEM-code-page output raise inside
    subprocess's reader THREAD, so run() returns normally and the traceback is printed by a
    background thread nobody watches.
    """
    navig_vault_compat = _load_compat()
    tree = ast.parse(inspect.getsource(navig_vault_compat._set_owner_only_fallback).lstrip())

    # Literals must come from the CODE, not from prose: the docstring deliberately warns
    # against `text=True`, so a substring search over the source reports it as present and
    # the assertion fires on the very comment explaining why it must not be there. (It did.)
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
    assert "/inheritance:r" in literals
    assert "/remove:g" in literals
    assert "Authenticated Users" in literals
    assert 0o600 in literals, "the POSIX branch must still chmod owner-only"

    decoded = [
        kw
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        for kw in call.keywords
        if kw.arg in {"text", "universal_newlines"}
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
    ]
    assert not decoded, (
        "icacls output must not be decoded: it writes localised group names in the OEM code "
        "page, and under Python's UTF-8 mode that decode raises inside subprocess's reader "
        "THREAD — run() returns normally and the traceback goes to a thread nobody watches."
    )
