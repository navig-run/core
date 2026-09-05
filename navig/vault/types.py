"""Compat shim -- the vault's dataclasses and enums (Credential, CredentialType, ...) now live in ``navig_vault.types``.

The vault engine is being extracted into the standalone ``navig-vault`` package so the two
cannot fork. This shim re-exports the ENTIRE module -- public and private names -- by
aliasing itself to the implementation, rather than doing ``from navig_vault.types import *``.

That distinction is load-bearing, not tidiness. ``core/tests/conftest.py`` ASSIGNS module
globals in the vault package to reset state between test modules, and there are ~87 patches
of ``navig.vault.core.get_vault`` and friends. Under a star-import shim those assignments
would land on the shim while the real module kept its own value, and the symptom would be
flaky Windows file locks in unrelated tests rather than a clean failure.
"""
from __future__ import annotations

import sys as _sys

try:
    from navig_vault import types as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a bare install
    raise ImportError(
        "NAVIG's vault requires the navig-vault engine. Install it with: pip install navig-vault"
    ) from exc

_sys.modules[__name__] = _impl
