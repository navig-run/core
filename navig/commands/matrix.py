"""Compat shim -- `navig matrix` now lives in ``navig_matrix.commands.matrix`` (navig-matrix).

Aliases itself to the implementation (not a re-export), so ``navig.commands.matrix.<name>``
patches and the registration map (``"matrix": ("navig.commands.matrix", "matrix_app")``)
reach the same module object the plugin's commands run — including the ``mx`` alias and the
tests that import ``matrix_app`` / ``room_app`` / ``admin_app`` from here.
"""
from __future__ import annotations

import sys as _sys

try:
    import navig_matrix.commands.matrix as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "`navig matrix` requires the navig-matrix package. "
        "Install it with: pip install navig-matrix"
    ) from exc

_sys.modules[__name__] = _impl
