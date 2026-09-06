"""Compat shim -- authenticated browser sessions now live in ``navig_vault.sessions``.

Aliases itself to the implementation rather than re-exporting it, so the module object is
IDENTICAL. That is load-bearing here above all: ``core/tests/conftest.py`` ASSIGNS
``navig.vault.core._vault = None`` to close SQLite handles between test modules (Windows
leaks ``vault.db`` handles otherwise), and ~87 tests patch ``navig.vault.core.get_vault``
and friends by dotted path. Under a re-export those writes would land on a copy while the
real singleton stayed live -- surfacing as a file lock or an unpatched call somewhere else
entirely, not as a clean failure here.
"""
from __future__ import annotations

import sys as _sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - never executed; exists for static analysis only
    # Static analysers cannot follow ``sys.modules[__name__] = _impl``; without this the
    # module reads as empty and every attribute reached through the old path is reported
    # missing. See the py.typed marker in navig-vault, which this depends on.
    from navig_vault.sessions import *  # noqa: F401,F403

try:
    from navig_vault import sessions as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a bare install
    raise ImportError(
        "NAVIG's vault requires the navig-vault engine. Install it with: pip install navig-vault"
    ) from exc

_sys.modules[__name__] = _impl
