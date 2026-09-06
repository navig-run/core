"""Compat shim -- the vault's key derivation and AES-GCM primitives now live in ``navig_vault.crypto``.

Aliases itself to the implementation rather than re-exporting it, so the module object is
IDENTICAL. That is required, not stylistic: ``core/tests/conftest.py`` assigns vault module
globals to close SQLite handles between test modules (Windows leaks ``vault.db`` handles
otherwise), and ~87 tests patch dotted paths into this package. A star-import copy would take
those assignments and patches while the real module kept its own values, and the symptom
would be a file lock or an unpatched call somewhere else entirely.

See ``core/tests/vault/test_vault_shim_identity.py``, which fails the build if this is ever
"simplified".
"""
from __future__ import annotations

import sys as _sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - never executed; exists for static analysis only
    # Static analysers cannot follow ``sys.modules[__name__] = _impl``: after the alias the
    # module IS the implementation at runtime, but on disk this file declares only `_impl`
    # and `_sys`. So every attribute access through the old path -- e.g. core.py doing
    # `_validators_mod.get_validator(...)` -- reads as "module has no attribute", and the
    # module-attr guard reports a call that actually works. Re-exporting here gives the
    # analyser the real names while runtime still gets the identity alias below.
    from navig_vault.crypto import *  # noqa: F401,F403

try:
    from navig_vault import crypto as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a bare install
    raise ImportError(
        "NAVIG's vault requires the navig-vault engine. Install it with: pip install navig-vault"
    ) from exc

_sys.modules[__name__] = _impl
