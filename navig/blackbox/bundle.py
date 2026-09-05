"""Compat shim — blackbox bundle now lives in ``navig_blackbox.bundle``.

Install with ``pip install navig[blackbox]``. Transparently re-exports the whole module
(including ``_default_log_files`` / ``_BUNDLE_EXT`` / ``_LOG_TAIL_LINES``).
"""
from __future__ import annotations

import sys as _sys

try:
    from navig_blackbox import bundle as _impl
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "NAVIG Blackbox requires the navig-blackbox engine. "
        "Install it with: pip install navig[blackbox]"
    ) from exc

_sys.modules[__name__] = _impl
