"""Compat shim — blackbox crash recorder now lives in ``navig_blackbox.crash``.

Install with ``pip install navig[blackbox]``. Transparently re-exports the whole module.
"""
from __future__ import annotations

import sys as _sys

try:
    from navig_blackbox import crash as _impl
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "NAVIG Blackbox requires the navig-blackbox engine. "
        "Install it with: pip install navig[blackbox]"
    ) from exc

_sys.modules[__name__] = _impl
