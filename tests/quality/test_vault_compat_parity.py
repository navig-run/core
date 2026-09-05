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


# ── the atomic write's Windows retry policy ──────────────────────────────────
#
# Found by comparing the two implementations rather than by a failure: core's
# atomic_write_text retries a PermissionError up to 3 times with exponential backoff --
# the documented Windows case where antivirus or a backup agent holds the destination for
# a moment -- and the standalone fallback tried exactly once. So a STANDALONE navig-vault
# raised where a navig-backed one succeeded, on a secrets store, on the one platform where
# it happens.
#
# The constants are duplicated in _compat rather than imported, because that module's whole
# purpose is to work with navig absent. Duplicated constants are exactly the thing that
# drifts, so they are asserted equal here: changing core's retry policy without changing the
# fallback now fails the build instead of quietly leaving standalone users less resilient.


def test_atomic_retry_constants_match_core() -> None:
    from navig.core import yaml_io

    compat = _load_compat()
    assert compat._ATOMIC_REPLACE_RETRIES == yaml_io._ATOMIC_REPLACE_RETRIES, (
        "navig_vault/_compat.py retries a locked atomic write a different number of times "
        "than navig does. A standalone vault must be no less resilient than a navig-backed "
        "one -- it is the same secrets file."
    )
    assert compat._ATOMIC_REPLACE_BACKOFF_BASE == yaml_io._ATOMIC_REPLACE_BACKOFF_BASE


def test_the_fallback_actually_retries_a_transient_lock(monkeypatch, tmp_path) -> None:
    """Constants matching is not enough -- the loop has to use them.

    Simulates the Windows case directly: os.replace fails twice with PermissionError, then
    succeeds. The write must survive, because that is what navig's own writer does.
    """
    compat = _load_compat()
    monkeypatch.setattr(compat.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(compat.time, "sleep", lambda _s: None)

    real_replace = compat.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "locked by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(compat.os, "replace", flaky)

    target = tmp_path / "vault.salt"
    compat._atomic_write_fallback(target, "secret-material")

    assert calls["n"] == 3, f"expected 3 attempts, made {calls['n']}"
    assert target.read_text(encoding="utf-8") == "secret-material"
    assert [q.name for q in tmp_path.iterdir()] == ["vault.salt"], (
        "a temp file was left behind by a retried write"
    )


def test_the_fallback_gives_up_rather_than_looping_forever(monkeypatch, tmp_path) -> None:
    """A permanently locked destination must raise, not spin."""
    compat = _load_compat()
    monkeypatch.setattr(compat.sys, "platform", "win32", raising=False)
    monkeypatch.setattr(compat.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def always_locked(src, dst):
        calls["n"] += 1
        raise PermissionError(13, "locked")

    monkeypatch.setattr(compat.os, "replace", always_locked)

    with pytest.raises(PermissionError):
        compat._atomic_write_fallback(tmp_path / "f.txt", "x")
    assert calls["n"] == compat._ATOMIC_REPLACE_RETRIES
    assert list(tmp_path.iterdir()) == [], "temp files left behind after exhausting retries"


def test_posix_does_not_retry_a_real_permission_error(monkeypatch, tmp_path) -> None:
    """On POSIX a PermissionError means what it says -- retrying just delays the error."""
    compat = _load_compat()
    monkeypatch.setattr(compat.sys, "platform", "linux", raising=False)
    calls = {"n": 0}

    def denied(src, dst):
        calls["n"] += 1
        raise PermissionError(13, "denied")

    monkeypatch.setattr(compat.os, "replace", denied)

    with pytest.raises(PermissionError):
        compat._atomic_write_fallback(tmp_path / "f.txt", "x")
    assert calls["n"] == 1, f"POSIX should not retry; made {calls['n']} attempts"
