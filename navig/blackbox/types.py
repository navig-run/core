"""Compat shim — blackbox types now live in ``navig_blackbox.types``.

Install with ``pip install navig[blackbox]``. This shim transparently re-exports the ENTIRE
module (public + private names) by aliasing itself to the engine module, so existing importers
— including those reaching for internals like ``_SEVERITY`` — keep working unchanged.
"""
from __future__ import annotations

import sys as _sys

try:
    from navig_blackbox import types as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a bare install
    raise ImportError(
        "NAVIG Blackbox requires the navig-blackbox engine. "
        "Install it with: pip install navig[blackbox]"
    ) from exc

_sys.modules[__name__] = _impl
